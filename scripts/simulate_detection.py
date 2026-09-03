"""
Simulační studie detekce anomálií — jediný způsob, jak dostat absolutní čísla.

Na reálných datech nejde změřit senzitivitu ani podíl planých poplachů, protože
neexistuje seznam všech skutečných epidemií („ground truth“). Standardní řešení
v oboru (přesně takhle validovala metodu Noufaily et al. 2012): nagenerovat
syntetické řady se známými vlastnostmi, injektovat do nich epidemie známé
velikosti a měřit, co detektor chytí.

Generátor řad: negativně-binomické měsíční počty (gamma–Poisson) se sezónností
(kosinus, poměr vrchol/údolí ≈ 2,2, náhodná fáze) — parametry volené tak, aby
pokryly spektrum reálných ISIN řad od vzácných nemocí (~2 případy/měsíc) po
běžné (~200/měsíc), s mírnou i silnou naddisperzí.

Injektovaná epidemie: velikost ν se měří v násobcích směrodatné odchylky
baseline (σ = √(φμ)) — stejná konvence jako Noufaily — a případy navíc se
rozloží do tří měsíců (nástup 30 %, vrchol 50 %, doznívání 20 %).

Metriky (pro oba modely baseline, „okna“ i „plny“, na týchž řadách):
  FPR  — podíl signálních měsíců v řadách BEZ epidemie (má se blížit 1 %)
  POD  — pravděpodobnost, že epidemie vyvolá signál v některém ze svých 3 měsíců
  zpoždění — kolikátý měsíc epidemie signál poprvé zvedl

Použití:
    python scripts/simulate_detection.py            # plná studie (~2 min)
    python scripts/simulate_detection.py --rychla   # hrubší, ~4× méně replikací

Výstup: tabulky na stdout + CSV v $DATA_DIR/analysis/simulation_results.csv.
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_anomalies import farrington_score  # noqa: E402  (testujeme ostrý kód)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))

N_MONTHS = 96            # 8 let, jako ISIN 2018–2025
SCORE_FROM = 72          # skóruje se posledních 24 měsíců (dost historie pro oba modely)
SEASON_AMP = 0.4         # poměr vrchol/údolí ≈ e^{2×0,4} ≈ 2,2
LEVELS = [2, 10, 50, 200]
DISPERSIONS = [1.5, 3.0]
NU_SIZES = [1, 2, 3, 5, 10]      # velikost epidemie v násobcích σ baseline
OUTBREAK_SHAPE = np.array([0.3, 0.5, 0.2])  # nástup / vrchol / doznívání


def gen_series(rng, level: float, phi: float) -> tuple[np.ndarray, np.ndarray]:
    """NB řada se sezónností; vrací (počty, střední hodnoty μ)."""
    peak = rng.integers(0, 12)
    m = np.arange(N_MONTHS)
    mu = level * np.exp(SEASON_AMP * np.cos(2 * np.pi * (m - peak) / 12))
    k = level / (phi - 1)                      # gamma tvar → Var ≈ φμ na úrovni level
    lam = rng.gamma(shape=k, scale=mu / k)
    return rng.poisson(lam).astype(float), mu


def inject(rng, counts: np.ndarray, mu: np.ndarray, start: int, nu: float, phi: float):
    """Přidá epidemii velikosti ν·σ rozloženou do 3 měsíců; vrací novou řadu."""
    sigma = np.sqrt(phi * mu[start])
    total = rng.poisson(max(nu * sigma, 0.1))
    extra = rng.multinomial(total, OUTBREAK_SHAPE)
    out = counts.copy()
    out[start:start + 3] += extra
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rychla", action="store_true", help="méně replikací (rychlý odhad)")
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()

    n_clean = 40 if args.rychla else 150      # řad bez epidemie na buňku
    n_out = 15 if args.rychla else 60         # epidemií na buňku a velikost
    rng = np.random.default_rng(args.seed)
    models = ["okna", "plny"]
    rows = []

    for level in LEVELS:
        for phi in DISPERSIONS:
            # FPR: čisté řady, oba modely na týchž datech
            for _ in range(n_clean):
                counts, _mu = gen_series(rng, level, phi)
                for t0 in range(SCORE_FROM, N_MONTHS):
                    for model in models:
                        res = farrington_score(counts, t0, model=model)
                        if res and res["type"] == "glm":
                            rows.append({"level": level, "phi": phi, "model": model,
                                         "kind": "clean", "nu": 0,
                                         "signal": res["signal"], "delay": None})
            # POD: injektované epidemie
            for nu in NU_SIZES:
                for _ in range(n_out):
                    counts, mu = gen_series(rng, level, phi)
                    start = int(rng.integers(SCORE_FROM, N_MONTHS - 2))
                    dosed = inject(rng, counts, mu, start, nu, phi)
                    for model in models:
                        delay = None
                        for off in range(3):
                            res = farrington_score(dosed, start + off, model=model)
                            if res and res["signal"]:
                                delay = off
                                break
                        rows.append({"level": level, "phi": phi, "model": model,
                                     "kind": "outbreak", "nu": nu,
                                     "signal": delay is not None, "delay": delay})

    df = pd.DataFrame(rows)
    out_dir = DATA_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "simulation_results.csv", index=False)

    clean = df[df.kind == "clean"]
    print("=== FPR — podíl planých poplachů (nominálně ~1 %) ===")
    print((clean.groupby(["model", "level"]).signal.mean() * 100)
          .unstack().round(1).to_string(), "\n")

    ob = df[df.kind == "outbreak"]
    print("=== POD — pravděpodobnost detekce epidemie podle velikosti ν·σ ===")
    print((ob.groupby(["model", "nu"]).signal.mean() * 100)
          .unstack().round(0).to_string(), "\n")

    print("=== POD podle úrovně baseline (ν=3) ===")
    print((ob[ob.nu == 3].groupby(["model", "level"]).signal.mean() * 100)
          .unstack().round(0).to_string(), "\n")

    det = ob[ob.signal]
    print("=== zpoždění detekce (měsíc epidemie, kdy přišel první signál) ===")
    print((det.groupby(["model", "delay"]).size()
           .unstack(fill_value=0)).to_string())
    print(f"\ncelkem skóre: {len(df):,} → {out_dir / 'simulation_results.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

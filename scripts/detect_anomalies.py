"""
Detekce anomálií nad ISIN — systém včasného varování (Farrington/Noufaily).

ISIN dává přes 1 500 měsíčních časových řad (114 diagnóz × 14 krajů + celá ČR),
které nikdo nesleduje očima. Tenhle skript pro každou řadu spočítá očekávanou
endemickou hladinu a označí měsíce, kdy hlášený počet překročil prahovou mez.

Metoda je Farringtonův algoritmus (Farrington et al. 1996) s vylepšeními podle
Noufaily et al. 2012 — stejný postup, který týdně běží nad tisíci řadami v UKHSA
a jehož referenční implementací je R balík `surveillance`. Zde reimplementace
v čistém numpy: model má jen dva parametry (konstanta + trend), takže IRLS je
pár řádků a pipeline nepotřebuje těžkou závislost typu statsmodels.

Jak se skóruje jeden měsíc jedné řady:

1.  Baseline tvoří stejná část roku v minulých letech (±1 měsíc kolem stejného
    kalendářního měsíce) — sezónnost tak řeší výběr dat, ne složitý model.
2.  Na baseline se nafituje kvazi-Poissonův GLM s lineárním trendem. „Kvazi“
    proto, že skutečné počty mají větší rozptyl než Poisson; podcenit rozptyl
    znamená falešné poplachy. Trend zůstává jen tehdy, když je průkazný
    a nepřestřeluje (Farringtonovo pravidlo).
3.  Minulé epidemie v baseline se převáží dolů (podle Anscombeho reziduí) —
    jinak by se systém naučil, že loňská epidemie je normál, a letos by mlčel.
4.  Práh je horní mez predikčního intervalu na mocninné škále 2/3 (stabilnější
    u malých počtů). Signál = pozorování nad prahem; síla se hlásí jako
    exceedance skóre (pozorované − očekávané) / (práh − očekávané).

Řady, kde je nemoc tak vzácná, že GLM nedává smysl (spalničky, záškrt…), mají
vlastní pravidlo: baseline prakticky nulová → hlásí se každý shluk případů.

Použití:
    python scripts/detect_anomalies.py                  # oskóruje poslední měsíc, zapíše JSON
    python scripts/detect_anomalies.py --backtest       # oskóruje celou historii do CSV
    python scripts/detect_anomalies.py --backtest --diagnoza "Dávivý kašel [pertussis]"

Proměnné prostředí: DATA_DIR (vstupní CSV), OUTPUT_DIR (kam psát JSON) —
stejný kontrakt jako generate_json.py.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
CHARTS_OUT = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

# ── parametry metody ─────────────────────────────────────────────────────────
Z_QUANTILE = 2.326      # 99. percentil — práh „1 falešný poplach na 100 klidných měsíců“
REWEIGHT_LIMIT = 2.58   # Anscombeho reziduum, od kterého se bod v baseline převažuje (Noufaily)
HALF_WINDOW = 1         # ±1 měsíc kolem stejného kalendářního měsíce
MIN_YEARS = 3           # méně let historie → řada se nehodnotí
MIN_CASES_GLM = 3       # signál z GLM se nehlásí pod 3 případy (šum u malých čísel)
RARE_TOTAL = 5          # suma celé historie ≤ 5 → „vzácná“ řada, GLM nedává smysl
RARE_ALERT = 2          # u vzácné řady se hlásí ≥ 2 případy v měsíci (1 import je běžný)

REGION_UNKNOWN = "CZ999"  # „neuvedeno“ — patří do součtu ČR, samostatně se neskóruje


# ── kvazi-Poissonův GLM (IRLS) ───────────────────────────────────────────────

def fit_quasipoisson(X: np.ndarray, y: np.ndarray, prior_w: np.ndarray | None = None):
    """
    Kvazi-Poissonův GLM s log-linkem přes IRLS.

    prior_w jsou váhy pozorování (Var(y_i) = φ·μ_i / w_i) — přes ně se dělá
    Noufailyové převážení minulých epidemií. Vrací (mu, beta, cov, phi, hat):
    cov už je škálovaná disperzí φ, hat je diagonála projekční matice
    (potřebná ke standardizaci reziduí).
    """
    n, p = X.shape
    w = np.ones(n) if prior_w is None else prior_w
    beta = np.zeros(p)
    beta[0] = np.log(max(y.mean(), 0.1))

    for _ in range(100):
        eta = np.clip(X @ beta, -30, 30)
        mu = np.exp(eta)
        W = w * mu                       # IRLS váha pro log-link
        z = eta + (y - mu) / mu          # pracovní odezva
        XtW = X.T * W
        A = XtW @ X
        beta_new = np.linalg.solve(A, XtW @ z)
        if np.max(np.abs(beta_new - beta)) < 1e-8:
            beta = beta_new
            break
        beta = beta_new

    eta = np.clip(X @ beta, -30, 30)
    mu = np.exp(eta)
    W = w * mu
    A = (X.T * W) @ X
    A_inv = np.linalg.inv(A)

    pearson = (y - mu) * np.sqrt(w / mu)
    phi = max(1.0, float(pearson @ pearson) / max(n - p, 1))  # disperze, min. 1 (Poisson)
    cov = A_inv * phi
    hat = np.clip(np.einsum("ij,jk,ik->i", X, A_inv, X) * W, 0, 0.999)
    return mu, beta, cov, phi, hat


def _anscombe(y: np.ndarray, mu: np.ndarray, phi: float, hat: np.ndarray) -> np.ndarray:
    """Standardizovaná Anscombeho rezidua — na nich stojí převažování epidemií."""
    r = 1.5 * (np.power(y, 2 / 3) - np.power(mu, 2 / 3)) / np.power(mu, 1 / 6)
    return r / np.sqrt(phi * (1 - hat))


# ── skórování jedné řady ─────────────────────────────────────────────────────

def farrington_score(counts: np.ndarray, t0: int) -> dict | None:
    """
    Oskóruje měsíc t0 řady `counts` (kompletní měsíční mřížka, index = pořadí
    měsíce od začátku dat). Vrací dict s výsledkem, nebo None, když řadu nelze
    hodnotit (málo historie).
    """
    # Baseline: stejné kalendářní měsíce v minulých letech ± HALF_WINDOW.
    idx, years_used = [], set()
    k = 1
    while True:
        anchor = t0 - 12 * k
        if anchor + HALF_WINDOW < 0:
            break
        for d in range(-HALF_WINDOW, HALF_WINDOW + 1):
            t = anchor + d
            if 0 <= t < t0:
                idx.append(t)
                years_used.add(k)
        k += 1
    if len(years_used) < MIN_YEARS:
        return None

    idx = np.array(sorted(idx))
    y = counts[idx].astype(float)
    y0 = float(counts[t0])

    history_total = float(counts[:t0].sum())
    if history_total <= RARE_TOTAL:
        # GLM nad samými nulami nedává smysl — vzácná nemoc, hlásí se shluk.
        return {
            "type": "rare",
            "observed": y0,
            "expected": 0.0,
            "threshold": float(RARE_ALERT - 0.5),
            "score": None,
            "signal": y0 >= RARE_ALERT,
            "n_baseline": len(idx),
        }

    # Čas škálujeme na roky, ať je koeficient trendu čitelný a IRLS stabilní.
    t_scale = (idx - t0) / 12.0
    X_trend = np.column_stack([np.ones(len(idx)), t_scale])
    X_const = np.ones((len(idx), 1))

    def fit_with_trend_rule(prior_w=None):
        """Farringtonovo pravidlo: trend jen průkazný a nepřestřelující."""
        mu, beta, cov, phi, hat = fit_quasipoisson(X_trend, y, prior_w)
        se_trend = np.sqrt(max(cov[1, 1], 0))
        mu0 = float(np.exp(np.clip(beta[0], -30, 30)))  # predikce v t0 (t_scale=0)
        significant = se_trend > 0 and abs(beta[1]) / se_trend > 1.96
        if significant and mu0 <= max(y.max(), 1.0):
            x0 = np.array([1.0, 0.0])
            return mu, beta, cov, phi, hat, mu0, x0
        mu, beta, cov, phi, hat = fit_quasipoisson(X_const, y, prior_w)
        mu0 = float(np.exp(np.clip(beta[0], -30, 30)))
        return mu, beta, cov, phi, hat, mu0, np.array([1.0])

    mu, beta, cov, phi, hat, mu0, x0 = fit_with_trend_rule()

    # Převážení minulých epidemií a jeden refit (Noufaily 2012).
    resid = _anscombe(y, mu, phi, hat)
    if np.any(resid > REWEIGHT_LIMIT):
        w = np.where(resid > REWEIGHT_LIMIT, resid ** -2, 1.0)
        w = w * len(w) / w.sum()
        mu, beta, cov, phi, hat, mu0, x0 = fit_with_trend_rule(prior_w=w)

    # Práh: horní mez predikčního intervalu na škále 2/3 (Farrington 1996).
    var_mu0 = mu0 ** 2 * float(x0 @ cov[: len(x0), : len(x0)] @ x0)
    V = phi * mu0 + var_mu0
    if mu0 > 0 and V > 0:
        U = mu0 * (1 + (2 / 3) * Z_QUANTILE * np.sqrt(V) / mu0) ** 1.5
    else:
        U = mu0 + Z_QUANTILE * np.sqrt(max(V, 1.0))

    # Podlaha prahu: u řady, jejíž baseline okna jsou skoro samé nuly, vyjde
    # U ≈ 0 a jmenovatel skóre se blíží nule — šest případů něčeho sporadického
    # by pak mělo skóre v tisících a přeskočilo skutečné epidemie. Práh jednoho
    # případu drží skóre ve významu „kolikrát nad minimálním detekovatelným
    # shlukem“, aniž by signál zrušil.
    U = max(float(U), 1.0)

    score = (y0 - mu0) / (U - mu0) if U > mu0 else None
    signal = bool(score is not None and score > 1 and y0 >= MIN_CASES_GLM)
    return {
        "type": "glm",
        "observed": y0,
        "expected": round(mu0, 2),
        "threshold": round(float(U), 2),
        "score": round(float(score), 2) if score is not None else None,
        "signal": signal,
        "n_baseline": len(idx),
    }


# ── data ─────────────────────────────────────────────────────────────────────

def load_series() -> tuple[pd.DataFrame, list[str], int]:
    """
    Vrátí (long tabulka diagnóza×kraj×měsíc, seznam period 'YYYY-MM', počet měsíců).
    Kraj 'CZ' je celostátní součet (včetně CZ999 „neuvedeno“, které se jinak
    samostatně neskóruje — případ bez kraje pořád je případ v ČR).
    """
    path = DATA_DIR / "isin" / "isin_infekcni_nemoci.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()

    grouped = (df.groupby(["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev",
                           "rok", "mesic"])["pocet_pripadu"].sum().reset_index())

    cz = (df.groupby(["diagnoza", "diagnoza_nazev", "rok", "mesic"])["pocet_pripadu"]
            .sum().reset_index())
    cz["kraj_kod"], cz["kraj_nazev"] = "CZ", "Česká republika"

    long = pd.concat([grouped[grouped.kraj_kod != REGION_UNKNOWN], cz], ignore_index=True)

    y_min, m_min = int(df.rok.min()), int(df[df.rok == df.rok.min()].mesic.min())
    y_max, m_max = int(df.rok.max()), int(df[df.rok == df.rok.max()].mesic.max())
    n_months = (y_max - y_min) * 12 + (m_max - m_min) + 1
    periods = []
    y, m = y_min, m_min
    for _ in range(n_months):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    long["t"] = (long.rok - y_min) * 12 + (long.mesic - m_min)
    return long, periods, n_months


def build_grid(long: pd.DataFrame, n_months: int):
    """Generátor (meta, counts) — kompletní mřížka s nulami tam, kde chybí řádek.
    Chybějící měsíc v agregovaném hlášení znamená nula případů, ne díru v datech."""
    for (dg, dg_name, kraj, kraj_name), g in long.groupby(
            ["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev"]):
        counts = np.zeros(n_months)
        counts[g.t.to_numpy()] = g.pocet_pripadu.to_numpy()
        yield {"diagnoza": dg, "diagnoza_nazev": dg_name,
               "kraj_kod": kraj, "kraj_nazev": kraj_name}, counts


# ── běhy ─────────────────────────────────────────────────────────────────────

def run_current(long, periods, n_months) -> int:
    t0 = n_months - 1
    signals, scored, skipped = [], 0, 0
    for meta, counts in build_grid(long, n_months):
        res = farrington_score(counts, t0)
        if res is None:
            skipped += 1
            continue
        scored += 1
        if res["signal"]:
            signals.append({**meta, **{k: v for k, v in res.items() if k != "signal"}})

    signals.sort(key=lambda s: (s["score"] is None, -(s["score"] or 0), -s["observed"]))
    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_period": periods[t0],
        "method": "Farrington–Noufaily, kvazi-Poisson GLM, 99. percentil",
        "n_series_scored": scored,
        "n_series_skipped": skipped,
        "n_signals": len(signals),
        "signals": signals,
    }
    CHARTS_OUT.mkdir(parents=True, exist_ok=True)
    path = CHARTS_OUT / "anomaly_signals.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[{periods[t0]}] oskórováno {scored} řad ({skipped} přeskočeno), "
          f"signálů {len(signals)} → {path}")
    for s in signals[:15]:
        print(f"  {s['diagnoza_nazev'][:45]:<45} {s['kraj_nazev']:<22} "
              f"{s['observed']:>6.0f} (oček. {s['expected']:>7.1f}, práh {s['threshold']:>7.1f}, "
              f"skóre {s['score'] if s['score'] is not None else '—'})")
    return 0


def run_backtest(long, periods, n_months, diagnoza: str | None) -> int:
    if diagnoza:
        long = long[long.diagnoza_nazev == diagnoza]
        if long.empty:
            print(f"Diagnóza „{diagnoza}“ v datech není.", file=sys.stderr)
            return 1
    rows = []
    start = 12 * MIN_YEARS  # skórovat lze až s MIN_YEARS lety historie
    for meta, counts in build_grid(long, n_months):
        for t0 in range(start, n_months):
            res = farrington_score(counts, t0)
            if res is None:
                continue
            rows.append({**meta, "period": periods[t0], **res})
    out_dir = DATA_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "anomaly_backtest.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    n_sig = sum(r["signal"] for r in rows)
    print(f"Backtest: {len(rows)} skóre, {n_sig} signálů → {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true",
                    help="oskórovat celou historii (výstup CSV), ne jen poslední měsíc")
    ap.add_argument("--diagnoza", default=None,
                    help="omezit backtest na jednu diagnózu (přesný název)")
    args = ap.parse_args()

    long, periods, n_months = load_series()
    if args.backtest:
        return run_backtest(long, periods, n_months, args.diagnoza)
    return run_current(long, periods, n_months)


if __name__ == "__main__":
    sys.exit(main())

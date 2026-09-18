"""
Sezónní prahy chřipky (MEM) nad českou řadou ILI → flu_mem.json.

Vstup: ILI na 100 tis. po týdnech. Historii dává WHO FluID (souvisle od
2009/10), nejčerstvější týdny ECDC ERVISS — je to tatáž řada hlášená dvěma
cestami (205 společných týdnů, největší rozdíl 1,4 %), jen ERVISS přibývá dřív.
Proč míra ILI, a ne laboratorní záchyty: záchytů je s rostoucím testováním
řádově víc (2019 ~1 000 vyšetřených vzorků ročně, 2024 ~64 500), takže práh
z minulých sezón by dnes svítil trvale. Míra na obyvatele tenhle posun nemá.

Výpočet samotný je v mem.py; tady je jen sestavení matice týden × sezóna,
volba δ a zápis výstupu.

Použití:
    python scripts/compute_mem.py
    python scripts/compute_mem.py --delta 2.8     # bez optimalizace δ

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mem

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

# Surveillance sezóna: ISO týden 40 až týden 20 následujícího roku (konvence ECDC).
SEASON_WEEKS = list(range(40, 53)) + list(range(1, 21))

# Sezóny, které do odhadu prahů nepatří. Klíč = rok, ve kterém sezóna začala.
EXCLUDED_SEASONS = {
    2009: "pandemie A(H1N1)pdm09 — vrchol už v listopadu, netypický průběh",
    2020: "covidová opatření — chřipka prakticky necirkulovala (vrchol ILI 13/100 tis.)",
    2021: "covidová opatření — potlačená a posunutá sezóna (vrchol ILI 24/100 tis.)",
    2025: "pokrytá populace ve FluID skočila z 5,4 na 8,5 mil. — změna hlásicího "
          "systému k ověření; do vyjasnění sezónu nepoužíváme",
}

DELTA_GRID = np.round(np.arange(2.0, 4.01, 0.1), 1)   # rozsah doporučený autory metody


def season_label(start_year: int) -> str:
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def load_ili() -> pd.DataFrame:
    """Týdenní ILI na 100 tis.: FluID, přepsané/doplněné ERVISS tam, kde ERVISS týden má."""
    fluid = pd.read_csv(DATA_DIR / "who" / "who_fluid_cz.csv")
    fluid = fluid[(fluid["vek"] == "All") & (fluid["ili_populace"] > 0)]
    series = pd.DataFrame({
        "rok": fluid["rok"], "tyden": fluid["tyden"],
        "ili": fluid["ili_pripady"] / fluid["ili_populace"] * 1e5, "zdroj": "WHO FluID",
    })

    erviss_path = DATA_DIR / "ecdc" / "erviss_ili_ari_cz.csv"
    if erviss_path.exists():
        e = pd.read_csv(erviss_path)
        e = e[(e["ukazatel"] == "ILI") & (e["vek"] == "total")]
        erviss = pd.DataFrame({
            "rok": e["tyden_iso"].str[:4].astype(int), "tyden": e["tyden_iso"].str[6:].astype(int),
            "ili": e["mira_na_100k"], "zdroj": "ECDC ERVISS",
        })
        series = pd.concat([series, erviss]).drop_duplicates(["rok", "tyden"], keep="last")

    series["sezona"] = np.where(series["tyden"] >= 27, series["rok"], series["rok"] - 1)
    return series.sort_values(["rok", "tyden"]).reset_index(drop=True)


def season_matrix(series: pd.DataFrame) -> pd.DataFrame:
    """
    Řádky = SEASON_WEEKS, sloupce = rok začátku sezóny. Týden 53 (jen některé
    roky) se zprůměruje s týdnem 52, aby měly všechny sezóny stejnou délku.
    """
    s = series.copy()
    s.loc[s["tyden"] == 53, "tyden"] = 52
    wide = s.groupby(["sezona", "tyden"])["ili"].mean().unstack("sezona")
    return wide.reindex(SEASON_WEEKS)


def optimize_delta(matrix: np.ndarray, seasons: list[str]) -> tuple[float, list[dict]]:
    """
    Leave-one-season-out. Pro každé δ: prahy z ostatních sezón, na vynechané
    sezóně se týden po týdnu porovná „hodnota ≥ epidemický práh“ s epidemickým
    obdobím, které v ní MEM našla. Vyhrává δ s nejvyšším Youdenovým indexem
    (senzitivita + specificita − 1); při shodě to bližší defaultu 2,8.
    """
    results = []
    for delta in DELTA_GRID:
        tp = fp = tn = fn = 0
        for j in range(matrix.shape[1]):
            rest = [k for k in range(matrix.shape[1]) if k != j]
            model = mem.fit(matrix[:, rest], [seasons[k] for k in rest], delta, max_seasons=None)
            x = mem.fill_inner_gaps(matrix[:, j])
            s, e = mem.epidemic_period(x, delta)
            truth = np.zeros(len(x), dtype=bool)
            truth[s:e + 1] = True
            alarm = x >= model.epidemic_threshold
            tp += int((alarm & truth).sum()); fn += int((~alarm & truth).sum())
            fp += int((alarm & ~truth).sum()); tn += int((~alarm & ~truth).sum())
        sens, spec = tp / (tp + fn), tn / (tn + fp)
        results.append({"delta": float(delta), "sensitivity": round(sens, 4),
                        "specificity": round(spec, 4), "youden": round(sens + spec - 1, 4)})
    best = max(results, key=lambda r: (r["youden"], -abs(r["delta"] - mem.DELTA)))
    return best["delta"], results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=float, default=None, help="pevné δ místo optimalizace")
    args = ap.parse_args()

    if not (DATA_DIR / "who" / "who_fluid_cz.csv").exists():
        print("  [flu_mem] chybí data/who/who_fluid_cz.csv — přeskakuji")
        return 0

    series = load_ili()
    wide = season_matrix(series)
    current = int(series["sezona"].max())

    # Do prahů jen uzavřené, úplné a nevyřazené sezóny.
    usable = [y for y in wide.columns
              if y != current and y not in EXCLUDED_SEASONS and wide[y].notna().all()]
    usable = usable[-mem.MAX_SEASONS:]
    labels = [season_label(y) for y in usable]
    matrix = wide[usable].to_numpy()

    if args.delta is not None:
        delta, grid = args.delta, []
    else:
        delta, grid = optimize_delta(matrix, labels)
    model = mem.fit(matrix, labels, delta)
    chosen = next((g for g in grid if g["delta"] == delta), None)

    latest = series.iloc[-1]
    this_season = series[series["sezona"] == current]
    above = this_season[this_season["ili"] >= model.epidemic_threshold]
    # Pořadí týdne epidemie dává smysl jen tehdy, když je nad prahem i poslední týden.
    epidemic_week = None
    if len(above) and latest["ili"] >= model.epidemic_threshold:
        epidemic_week = int(len(this_season.loc[above.index[0]:]))

    out = {
        "indicator": "ILI na 100 tis. obyvatel",
        "method": "Moving Epidemic Method (Vega et al. 2013, 2015)",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sorted(series["zdroj"].unique()),
        "delta": delta,
        "n_values_per_season": model.n_values,
        "seasons_used": model.seasons,
        "seasons_excluded": {season_label(y): why for y, why in EXCLUDED_SEASONS.items()},
        "thresholds": {
            "epidemic": round(model.epidemic_threshold, 2),
            "medium": round(model.intensity_thresholds[0], 2),
            "high": round(model.intensity_thresholds[1], 2),
            "very_high": round(model.intensity_thresholds[2], 2),
        },
        "epidemic_periods": {
            k: {"start_week": SEASON_WEEKS[s], "end_week": SEASON_WEEKS[e]}
            for k, (s, e) in model.periods.items()
        },
        "validation": chosen,
        "current": {
            "season": season_label(current),
            "week": f"{int(latest['rok'])}-W{int(latest['tyden']):02d}",
            "value": round(float(latest["ili"]), 2),
            "level": mem.classify(float(latest["ili"]), model),
            "epidemic_week": epidemic_week,
            "source": latest["zdroj"],
        },
        "season_weeks": SEASON_WEEKS,
        "history": {season_label(y): [None if pd.isna(v) else round(float(v), 2) for v in wide[y]]
                    for y in wide.columns if y >= usable[0]},
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "flu_mem.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    t = out["thresholds"]
    print(f"  [flu_mem] {len(labels)} sezón, δ={delta}; práh {t['epidemic']}, "
          f"intenzita {t['medium']} / {t['high']} / {t['very_high']}; "
          f"{out['current']['week']}: {out['current']['value']} → {out['current']['level']} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

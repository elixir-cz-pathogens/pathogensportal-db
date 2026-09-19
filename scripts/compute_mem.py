"""
Sezónní prahy chřipky (MEM) nad českými řadami ILI a ARI → flu_mem.json.

Dva ukazatele, oba jako týdenní míra na 100 tis. z hlášení praktických lékařů:

  ILI  chřipce podobné onemocnění (náhlý začátek + horečka/schvácenost +
       respirační příznak) — úzká definice, blízko skutečné chřipce. Vlna ji
       zvedne ~15× nad podzimní klid, proto je hlavním ukazatelem intenzity.
  ARI  akutní respirační infekce (stačí respirační příznak) — všechno, co se
       točí: rhinoviry, RSV, covid i chřipka. Vlna ji zvedne jen ~2×, ale je to
       číslo, ve kterém tradičně mluví česká hygiena, takže ho čtenář zná.

Historii dává WHO FluID (souvisle od 2009/10), nejčerstvější týdny ECDC ERVISS —
tatáž řada hlášená dvěma cestami (ILI: 205 společných týdnů, největší rozdíl
1,4 %), jen ERVISS přibývá dřív. Proč míry od lékařů, a ne laboratorní záchyty:
záchytů je s rostoucím testováním řádově víc (2019 ~1 000 vyšetřených vzorků
ročně, 2024 ~64 500), takže práh z minulých sezón by dnes svítil trvale.

Výpočet samotný je v mem.py; tady je jen sestavení matice týden × sezóna,
výběr sezón a zápis výstupu.

δ je pevně na standardních 2,8 (default R balíku, používá ho ECDC — prahy jsou
tak srovnatelné s jinými zeměmi). Optimalizace přes leave-one-season-out tu je,
ale jen na vyžádání: s deseti sezónami je Youdenova křivka zubatá (0,90 při 2,8,
0,93 při 3,9, 0,85 při 4,0) a rozdíl je v šumu. Navíc δ=3,9 dává epidemie dlouhé
7–9 týdnů a práh o čtvrtinu výš, tedy pozdější hlášení začátku sezóny. Citlivost
na δ se do výstupu zapisuje vždy, ať je vidět, o co se rozhodnutí opírá.

Použití:
    python scripts/compute_mem.py
    python scripts/compute_mem.py --optimize-delta   # δ podle Youdenova indexu
    python scripts/compute_mem.py --delta 3.0

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

NO_EPIDEMIC = "sezóna bez epidemie — vrchol {peak:.0f} nedosáhl epidemického prahu {thr:.0f} " \
              "odhadnutého z ostatních sezón; nemá epidemické týdny, ze kterých by šla počítat intenzita"

# Pod tímhle Youdenovým indexem (leave-one-season-out) epidemický práh sezónu od
# klidu spolehlivě neodliší a pásma intenzity by byla falešná přesnost. ILI má
# ~0,90; ARI ~0,42 — vlna se v ní ztrácí v celoročním pozadí jiných virů, takže
# se u ní na portálu ukazuje jen křivka s prahem, bez pásem.
MIN_YOUDEN_FOR_INTENSITY = 0.7

DELTA_GRID = np.round(np.arange(2.0, 4.01, 0.1), 1)   # rozsah doporučený autory metody


def season_label(start_year: int) -> str:
    return f"{start_year}/{(start_year + 1) % 100:02d}"


INDICATORS = {
    "ili": {"label": "ILI na 100 tis. obyvatel", "cases": "ili_pripady",
            "population": "ili_populace", "erviss": "ILI"},
    "ari": {"label": "ARI na 100 tis. obyvatel", "cases": "ari_pripady",
            "population": "ari_populace", "erviss": "ARI"},
}


def load_series(indicator: str) -> pd.DataFrame:
    """Týdenní míra na 100 tis.: FluID, přepsaná/doplněná ERVISS tam, kde ERVISS týden má."""
    spec = INDICATORS[indicator]
    fluid = pd.read_csv(DATA_DIR / "who" / "who_fluid_cz.csv")
    fluid = fluid[(fluid["vek"] == "All") & (fluid[spec["population"]] > 0)]
    series = pd.DataFrame({
        "rok": fluid["rok"], "tyden": fluid["tyden"],
        "mira": fluid[spec["cases"]] / fluid[spec["population"]] * 1e5, "zdroj": "WHO FluID",
    })

    erviss_path = DATA_DIR / "ecdc" / "erviss_ili_ari_cz.csv"
    if erviss_path.exists():
        e = pd.read_csv(erviss_path)
        e = e[(e["ukazatel"] == spec["erviss"]) & (e["vek"] == "total")]
        erviss = pd.DataFrame({
            "rok": e["tyden_iso"].str[:4].astype(int), "tyden": e["tyden_iso"].str[6:].astype(int),
            "mira": e["mira_na_100k"], "zdroj": "ECDC ERVISS",
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
    wide = s.groupby(["sezona", "tyden"])["mira"].mean().unstack("sezona")
    return wide.reindex(SEASON_WEEKS)


def seasons_without_epidemic(wide: pd.DataFrame, pool: list[int], delta: float) -> dict[int, str]:
    """
    Sezóny, ve kterých epidemie vůbec neproběhla: vrchol pod epidemickým prahem
    spočítaným z ostatních sezón. MEM v každé sezóně nějaké „epidemické období“
    najde vždycky — i v ploché řadě — a jeho hodnoty pak jdou do prahů intenzity.
    Sezóna 2013/14 (vrchol ILI 33, 48 laboratorních záchytů za celou zimu; data
    jsou úplná a ARI normální, takže nejde o chybu hlášení) tak sama zvedla práh
    „vysoké“ intenzity z 288 na 344, nad všechno, co kdy bylo naměřeno.
    Pravidlo je obecné a rozhoduje se z dat, ne ručním seznamem; opakuje se,
    dokud se množina nemění (vyřazení jedné sezóny posune práh pro ostatní).
    """
    dropped: dict[int, str] = {}
    while True:
        kept = [y for y in pool if y not in dropped]
        found = None
        for y in kept:
            rest = [k for k in kept if k != y]
            if len(rest) < 2:
                return dropped
            thr = mem.fit(wide[rest].to_numpy(), [season_label(k) for k in rest],
                          delta, max_seasons=None).epidemic_threshold
            if wide[y].max() < thr:
                found = (y, NO_EPIDEMIC.format(peak=wide[y].max(), thr=thr))
                break
        if found is None:
            return dropped
        dropped[found[0]] = found[1]


def _confusion(matrix: np.ndarray, seasons: list[str], delta: float) -> tuple[int, int, int, int]:
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
    return tp, fp, tn, fn


def optimize_delta(matrix: np.ndarray, seasons: list[str]) -> tuple[float, list[dict]]:
    """
    Leave-one-season-out. Pro každé δ: prahy z ostatních sezón, na vynechané
    sezóně se týden po týdnu porovná „hodnota ≥ epidemický práh“ s epidemickým
    obdobím, které v ní MEM našla. Vyhrává δ s nejvyšším Youdenovým indexem
    (senzitivita + specificita − 1); při shodě to bližší defaultu 2,8.
    """
    results = []
    for delta in DELTA_GRID:
        try:
            counts = _confusion(matrix, seasons, float(delta))
        except ValueError:
            # Při malém δ u ploché řady (ARI: týden nese ~3 % sezóny i mimo vlnu) vyjde
            # „epidemie“ od prvního týdne a nezbudou pre-epidemické hodnoty — δ nepoužitelné.
            continue
        tp, fp, tn, fn = counts
        sens, spec = tp / (tp + fn), tn / (tn + fp)
        results.append({"delta": float(delta), "sensitivity": round(sens, 4),
                        "specificity": round(spec, 4), "youden": round(sens + spec - 1, 4)})
    best = max(results, key=lambda r: (r["youden"], -abs(r["delta"] - mem.DELTA)))
    return best["delta"], results


def compute_indicator(indicator: str, delta: float, optimize: bool) -> dict:
    series = load_series(indicator)
    wide = season_matrix(series)
    current = int(series["sezona"].max())

    # Do prahů jen uzavřené, úplné a nevyřazené sezóny, ve kterých epidemie proběhla.
    pool = [y for y in wide.columns
            if y != current and y not in EXCLUDED_SEASONS and wide[y].notna().all()]
    no_epidemic = seasons_without_epidemic(wide, pool, delta)
    usable = [y for y in pool if y not in no_epidemic][-mem.MAX_SEASONS:]
    labels = [season_label(y) for y in usable]
    matrix = wide[usable].to_numpy()

    best_delta, grid = optimize_delta(matrix, labels)
    if optimize:
        delta = best_delta
    model = mem.fit(matrix, labels, delta)

    latest = series.iloc[-1]
    this_season = series[series["sezona"] == current]
    above = this_season[this_season["mira"] >= model.epidemic_threshold]
    # Pořadí týdne epidemie dává smysl jen tehdy, když je nad prahem i poslední týden.
    epidemic_week = None
    if len(above) and latest["mira"] >= model.epidemic_threshold:
        epidemic_week = int(len(this_season.loc[above.index[0]:]))

    validation = next((g for g in grid if g["delta"] == round(delta, 1)), None)
    return {
        "label": INDICATORS[indicator]["label"],
        "intensity_reliable": bool(validation and validation["youden"] >= MIN_YOUDEN_FOR_INTENSITY),
        "sources": sorted(series["zdroj"].unique()),
        "delta": delta,
        "n_values_per_season": model.n_values,
        "seasons_used": model.seasons,
        "seasons_excluded": {season_label(y): why
                             for y, why in sorted({**EXCLUDED_SEASONS, **no_epidemic}.items())},
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
        "validation": validation,
        "delta_sensitivity": grid,
        "current": {
            "season": season_label(current),
            "week": f"{int(latest['rok'])}-W{int(latest['tyden']):02d}",
            "value": round(float(latest["mira"]), 2),
            "level": mem.classify(float(latest["mira"]), model),
            "epidemic_week": epidemic_week,
            "source": latest["zdroj"],
        },
        "history": {season_label(y): [None if pd.isna(v) else round(float(v), 2) for v in wide[y]]
                    for y in wide.columns if y >= usable[0]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=float, default=mem.DELTA, help=f"default {mem.DELTA}")
    ap.add_argument("--optimize-delta", action="store_true",
                    help="zvolit δ leave-one-season-out podle Youdenova indexu")
    args = ap.parse_args()

    if not (DATA_DIR / "who" / "who_fluid_cz.csv").exists():
        print("  [flu_mem] chybí data/who/who_fluid_cz.csv — přeskakuji")
        return 0

    out = {
        "method": "Moving Epidemic Method (Vega et al. 2013, 2015)",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season_weeks": SEASON_WEEKS,
        "indicators": {k: compute_indicator(k, args.delta, args.optimize_delta) for k in INDICATORS},
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "flu_mem.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    for key, ind in out["indicators"].items():
        t, c = ind["thresholds"], ind["current"]
        print(f"  [flu_mem] {key.upper()}: {len(ind['seasons_used'])} sezón, δ={ind['delta']}; "
              f"práh {t['epidemic']}, intenzita {t['medium']} / {t['high']} / {t['very_high']}; "
              f"{c['week']}: {c['value']} → {c['level']}")
    print(f"  [flu_mem] → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

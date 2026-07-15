"""
Generuje JSON data soubory pro Chart.js grafy v Hugo.
Vstup:  data/mzcr/*.csv, data/ecdc/*.csv
Výstup: site/static/data/charts/*.json
"""

import json
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_IN = ROOT / "data"
DATA_OUT = ROOT / "site" / "static" / "data" / "charts"

COLORS = {
    "blue":   ("rgba(13,110,253,0.6)",  "rgb(13,110,253)"),
    "red":    ("rgba(220,53,69,0.6)",   "rgb(220,53,69)"),
    "orange": ("rgba(255,153,0,0.6)",   "rgb(255,153,0)"),
    "green":  ("rgba(25,135,84,0.6)",   "rgb(25,135,84)"),
    "purple": ("rgba(111,66,193,0.6)",  "rgb(111,66,193)"),
    "teal":   ("rgba(32,201,151,0.6)",  "rgb(32,201,151)"),
}


def ds(label, data, color="blue", chart_type="line", fill=False):
    bg, border = COLORS[color]
    return {
        "label": label,
        "data": data,
        "backgroundColor": bg,
        "borderColor": border,
        "borderWidth": 2,
        "fill": fill,
        "tension": 0.3,
        "pointRadius": 1,
        "type": chart_type,
    }


def to_weekly(df: pd.DataFrame, date_col="datum") -> pd.DataFrame:
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df["week"] = df[date_col].dt.to_period("W").dt.start_time
    return df


def save(name: str, obj: dict):
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    path = DATA_OUT / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"  [{name}] → {path}")


# ── 1. Epidemiologická křivka — týdenní nové případy + úmrtí ─────────────────
def covid_cases_weekly():
    df = pd.read_csv(DATA_IN / "mzcr" / "covid_pripady.csv")
    df = to_weekly(df)
    w = df.groupby("week").agg(
        pripady=("prirustkovy_pocet_nakazenych", "sum"),
        umrti=("prirustkovy_pocet_umrti", "sum"),
    ).reset_index()
    labels = w["week"].dt.strftime("%Y-%m-%d").tolist()
    save("covid_cases_weekly", {
        "labels": labels,
        "datasets": [
            ds("Nové případy (týden)", w["pripady"].tolist(), "blue", "bar"),
            ds("Úmrtí (týden)", w["umrti"].tolist(), "red", "line"),
        ],
    })


# ── 2. Hospitalizace — stav pacientů ─────────────────────────────────────────
def covid_hospitalization():
    df = pd.read_csv(DATA_IN / "mzcr" / "covid_hospitalizace.csv")
    df["datum"] = pd.to_datetime(df["datum"])
    # Downsampling na tydenni maximum (je to pocet, ne sum)
    df["week"] = df["datum"].dt.to_period("W").dt.start_time
    w = df.groupby("week")[["pocet_hosp", "jip", "upv", "ecmo"]].max().reset_index()
    labels = w["week"].dt.strftime("%Y-%m-%d").tolist()
    save("covid_hospitalization", {
        "labels": labels,
        "datasets": [
            ds("Hospitalizovaní celkem", w["pocet_hosp"].tolist(), "blue", fill=True),
            ds("JIP", w["jip"].tolist(), "orange"),
            ds("UPV (plicní ventilace)", w["upv"].tolist(), "red"),
            ds("ECMO", w["ecmo"].tolist(), "purple"),
        ],
    })


# ── 3. Testování — PCR pozitivita % ──────────────────────────────────────────
def covid_testing():
    df = pd.read_csv(DATA_IN / "mzcr" / "covid_testy.csv")
    df = to_weekly(df)
    w = df.groupby("week").agg(
        pcr=("pocet_PCR_testy", "sum"),
        pozit=("incidence_pozitivni", "sum"),
    ).reset_index()
    w["pozitivita"] = (w["pozit"] / w["pcr"] * 100).round(1).clip(0, 100)
    labels = w["week"].dt.strftime("%Y-%m-%d").tolist()
    save("covid_testing", {
        "labels": labels,
        "datasets": [
            ds("PCR pozitivita (%)", w["pozitivita"].tolist(), "green"),
        ],
    })


# ── 4. 7denní incidence na 100 000 ───────────────────────────────────────────
def covid_incidence():
    df = pd.read_csv(DATA_IN / "mzcr" / "covid_incidence.csv")
    df["datum"] = pd.to_datetime(df["datum"])
    df = df.sort_values("datum")
    # Každý 7. bod (nechceme zbytecne husty graf)
    df7 = df.iloc[::7]
    labels = df7["datum"].dt.strftime("%Y-%m-%d").tolist()
    save("covid_incidence", {
        "labels": labels,
        "datasets": [
            ds("7denní incidence / 100 000", df7["incidence_7_100000"].tolist(), "teal"),
        ],
    })


# ── 5. Souhrnné statistiky (pro info karty) ───────────────────────────────────
def covid_summary():
    df = pd.read_csv(DATA_IN / "mzcr" / "covid_pripady.csv")
    last = df.iloc[-1]
    save("covid_summary", {
        "celkem_nakazenych": int(last["kumulativni_pocet_nakazenych"]),
        "celkem_umrti":      int(last["kumulativni_pocet_umrti"]),
        "celkem_testu":      int(last["kumulativni_pocet_testu"]),
        "posledni_datum":    str(last["datum"]),
    })


# ─────────────────────────────────────────────────────────────────────────────
# SZÚ Influenza / respirační viry
# ─────────────────────────────────────────────────────────────────────────────

FLU_COLORS = {
    "Influenza A":           "blue",
    "Influenza A/H1N1pdm":  "blue",
    "Influenza A/H1":       "blue",
    "Influenza A/H3N2":     "teal",
    "Influenza B":          "red",
    "RSV":                  "orange",
    "Rhinovirus":           "green",
    "Coronavirus (sezónní)":"purple",
    "Adenovirus":           "orange",
    "Parainfluenza":        "teal",
    "Mycoplasma pneumoniae":"red",
    "Metapneumovirus":      "green",
}


def _load_influenza() -> pd.DataFrame:
    import glob
    files = sorted(glob.glob(str(DATA_IN / "szu" / "szu_influenza_*.csv")))
    if not files:
        return pd.DataFrame()
    dfs = [pd.read_csv(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    # Jen detekce virem (ne serologie/izolace)
    return df[df["kategorie"] == "Detekce viru"].copy()


# ── 6. Sezónní přehled — Influenza A + B celkem per sezóna ───────────────────
def flu_season_overview():
    df = _load_influenza()
    if df.empty:
        print("  [flu_season] žádná data, přeskakuji")
        return

    flu_viruses = [v for v in df["virus"].unique() if "Influenza" in v]
    flu_df = df[df["virus"].isin(flu_viruses)]

    # Normalizace: A + A/H1N1pdm + A/H3N2 → "Influenza A"; B → "Influenza B"
    def flu_group(v):
        if "B" in v:
            return "Influenza B"
        return "Influenza A (celkem)"

    flu_df = flu_df.copy()
    flu_df["flu_group"] = flu_df["virus"].apply(flu_group)
    by_season = flu_df.groupby(["sezona", "flu_group"])["pocet"].sum().unstack(fill_value=0)

    # Doplň chybějící sezóny (nulová chřipka v COVID roce 2020/21)
    all_seasons = sorted(df["sezona"].unique())
    by_season = by_season.reindex(all_seasons, fill_value=0)

    seasons = [s.replace("_", "/") for s in by_season.index.tolist()]
    a_data = by_season.get("Influenza A (celkem)", pd.Series(0, index=by_season.index)).tolist()
    b_data = by_season.get("Influenza B", pd.Series(0, index=by_season.index)).tolist()

    save("flu_season_overview", {
        "labels": seasons,
        "datasets": [
            ds("Influenza A (celkem)", a_data, "blue", "bar"),
            ds("Influenza B", b_data, "red", "bar"),
        ],
    })


# ── 7. Týdenní trend chřipky — poslední sezóna ───────────────────────────────
def flu_weekly_last_season():
    df = _load_influenza()
    if df.empty:
        print("  [flu_weekly] žádná data, přeskakuji")
        return

    last_season = sorted(df["sezona"].unique())[-1]
    season_df = df[df["sezona"] == last_season].copy()

    flu_viruses = [v for v in season_df["virus"].unique() if "Influenza" in v]
    flu_df = season_df[season_df["virus"].isin(flu_viruses)].copy()
    flu_df["flu_group"] = flu_df["virus"].apply(
        lambda v: "Influenza B" if "B" in v else "Influenza A (celkem)"
    )

    by_week = flu_df.groupby(["tyden_kt", "flu_group"])["pocet"].sum().unstack(fill_value=0).reset_index()
    by_week = by_week.sort_values("tyden_kt")
    labels = [f"KT {int(w)}" for w in by_week["tyden_kt"]]

    save("flu_weekly", {
        "season": last_season.replace("_", "/"),
        "labels": labels,
        "datasets": [
            ds("Influenza A", by_week.get("Influenza A (celkem)", pd.Series([0]*len(labels))).tolist(), "blue"),
            ds("Influenza B", by_week.get("Influenza B", pd.Series([0]*len(labels))).tolist(), "red"),
        ],
    })


# ── 8. Respirační viry — celkový přehled per sezóna ─────────────────────────
def flu_respiratory_all():
    df = _load_influenza()
    if df.empty:
        print("  [flu_resp] žádná data, přeskakuji")
        return

    TOP_VIRUSES = [
        "Influenza A/H3N2", "Influenza A/H1N1pdm", "Influenza B",
        "RSV", "Rhinovirus", "Adenovirus", "Parainfluenza",
        "Coronavirus (sezónní)", "Metapneumovirus", "Mycoplasma pneumoniae",
    ]
    color_cycle = ["blue", "red", "teal", "orange", "green", "purple",
                   "teal", "purple", "red", "orange"]

    mask = df["virus"].isin(TOP_VIRUSES)
    by_season_virus = df[mask].groupby(["sezona", "virus"])["pocet"].sum().unstack(fill_value=0)
    seasons = [s.replace("_", "/") for s in by_season_virus.index.tolist()]

    datasets = []
    for i, virus in enumerate(TOP_VIRUSES):
        if virus in by_season_virus.columns:
            color = color_cycle[i % len(color_cycle)]
            datasets.append(ds(virus, by_season_virus[virus].tolist(), color, "bar"))

    save("flu_respiratory_all", {
        "labels": seasons,
        "datasets": datasets,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Regionální data — SZÚ lab_tests z covid.db
# ─────────────────────────────────────────────────────────────────────────────

def _db_query(sql: str) -> pd.DataFrame:
    """Spustí SQL na covid.db přes CLI (conda sqlite3 má linking issue)."""
    import subprocess, io as _io
    db_path = str(DATA_IN / "covid.db")
    result = subprocess.run(
        ["sqlite3", "-csv", "-header", db_path, sql],
        capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0 or not result.stdout.strip():
        return pd.DataFrame()
    return pd.read_csv(_io.StringIO(result.stdout))


REGION_CLEAN = {
    "Praha + Středočeský kraj":  "Praha + Stř. Čechy",
    "Jihomoravský kraj":         "Jihomoravský",
    "Jihočeský kraj":            "Jihočeský",
    "Karlovarský kraj":          "Karlovarský",
    "Královéhradecký kraj":      "Královéhradecký",
    "Liberecký kraj":            "Liberecký",
    "Moravskoslezský kraj":      "Moravskoslezský",
    "Olomoucký kraj":            "Olomoucký",
    "Pardubický kraj":           "Pardubický",
    "Plzeňský kraj":             "Plzeňský",
    "Ústecký kraj":              "Ústecký",
    "Vysočina":                  "Vysočina",
}

# ── 9. Regionální chřipka — kumulativní total per region per rok ──────────────
def flu_regional_overview():
    df = _db_query("""
        SELECT Year, Region, SUM(Positive_Weekly) as pozitivni
        FROM lab_tests
        WHERE Region NOT LIKE '%Celk%'
          AND Region NOT LIKE '%Vysočina Olomoucký%'
          AND Region NOT LIKE '%Vysočina Vysočina%'
        GROUP BY Year, Region
        ORDER BY Year, pozitivni DESC
    """)
    if df.empty:
        print("  [flu_regional] zadna data"); return

    df["Region"] = df["Region"].map(REGION_CLEAN).fillna(df["Region"])
    years = sorted(df["Year"].unique())
    regions = list(REGION_CLEAN.values())

    color_cycle = ["blue", "red", "teal", "orange", "green", "purple",
                   "blue", "red", "teal", "orange", "green"]
    datasets = []
    for i, region in enumerate(regions):
        rdf = df[df["Region"] == region].set_index("Year")
        data = [int(rdf.loc[y, "pozitivni"]) if y in rdf.index else 0 for y in years]
        if sum(data) == 0:
            continue
        datasets.append(ds(region, data, color_cycle[i % len(color_cycle)], "bar"))

    save("flu_regional_overview", {
        "labels": [str(y) for y in years],
        "datasets": datasets,
    })


# ── 10. Regionální chřipka — aktuální týden (posledních 20 KT) ───────────────
def flu_regional_weekly():
    df = _db_query("""
        SELECT Year, Week, Region, SUM(Positive_Weekly) as pozitivni
        FROM lab_tests
        WHERE Region NOT LIKE '%Celk%'
          AND Region NOT LIKE '%Vysočina Olomoucký%'
          AND Region NOT LIKE '%Vysočina Vysočina%'
        GROUP BY Year, Week, Region
        ORDER BY Year, Week
    """)
    if df.empty:
        print("  [flu_regional_weekly] zadna data"); return

    df["Region"] = df["Region"].map(REGION_CLEAN).fillna(df["Region"])
    # Pouze sezóna 2024-2025 (KT40/2024 – KT36/2025)
    mask = ((df["Year"] == 2024) & (df["Week"] >= 40)) | \
           ((df["Year"] == 2025) & (df["Week"] <= 36))
    df = df[mask]
    df["label"] = df.apply(
        lambda r: f"KT{int(r.Week):02d}/{int(r.Year)}", axis=1
    )
    labels = df["label"].unique().tolist()
    # Seřaď správně (2024/KT40 → 2025/KT36)
    labels = sorted(labels, key=lambda x: (
        int(x.split("/")[1]),
        int(x.replace("KT","").split("/")[0])
    ))

    color_cycle = ["blue", "red", "teal", "orange", "green", "purple",
                   "blue", "red", "teal", "orange", "green"]
    datasets = []
    top_regions = (df.groupby("Region")["pozitivni"].sum()
                   .sort_values(ascending=False).head(6).index.tolist())

    for i, region in enumerate(top_regions):
        rdf = df[df["Region"] == region].set_index("label")
        data = [int(rdf.loc[lbl, "pozitivni"]) if lbl in rdf.index else 0
                for lbl in labels]
        datasets.append(ds(region, data, color_cycle[i % len(color_cycle)]))

    save("flu_regional_weekly", {
        "labels": labels,
        "datasets": datasets,
    })


# ─────────────────────────────────────────────────────────────────────────────
# COVID pacienti — věková a vakcinační analýza
# ─────────────────────────────────────────────────────────────────────────────

# ── 11. Případy a hospitalizace podle věkové skupiny ─────────────────────────
def covid_by_age():
    df = _db_query("""
        SELECT RokNarozeni as vek_skupina,
               COUNT(*) as pripady,
               SUM(CASE WHEN bin_Hospitalizace = 1 THEN 1 ELSE 0 END) as hospitalizace,
               SUM(CASE WHEN Umrti = '1' THEN 1 ELSE 0 END) as umrti
        FROM pacienti
        WHERE RokNarozeni IS NOT NULL AND RokNarozeni != ''
        GROUP BY RokNarozeni
        ORDER BY RokNarozeni
    """)
    if df.empty:
        print("  [covid_age] zadna data"); return

    # Seřaď věkové skupiny chronologicky (1920-1924, ..., 2020-2024)
    df = df.sort_values("vek_skupina")
    df["hosp_rate"] = (df["hospitalizace"] / df["pripady"] * 100).round(1)

    labels = df["vek_skupina"].tolist()
    save("covid_by_age", {
        "labels": labels,
        "datasets": [
            ds("Případy", df["pripady"].tolist(), "blue", "bar"),
            ds("Hospitalizace", df["hospitalizace"].tolist(), "orange", "bar"),
            ds("Úmrtí", df["umrti"].tolist(), "red", "bar"),
        ],
    })
    save("covid_hosp_rate_by_age", {
        "labels": labels,
        "datasets": [
            ds("Hospitalizační míra (%)", df["hosp_rate"].tolist(), "orange"),
        ],
    })


# ── 12. Případy a výsledky dle počtu dávek vakcíny ───────────────────────────
def covid_by_vaccination():
    df = _db_query("""
        SELECT
            CASE
                WHEN Datum_Ctvrta_davka IS NOT NULL THEN '4+ dávky'
                WHEN Datum_Treti_davka IS NOT NULL  THEN '3 dávky'
                WHEN Datum_Druha_davka IS NOT NULL  THEN '2 dávky'
                WHEN Datum_Prvni_davka IS NOT NULL  THEN '1 dávka'
                ELSE 'Nevakcinován'
            END as ockovani,
            COUNT(*) as pripady,
            SUM(CASE WHEN bin_Hospitalizace = 1 THEN 1 ELSE 0 END) as hospitalizace,
            SUM(CASE WHEN Umrti = '1' THEN 1 ELSE 0 END) as umrti
        FROM pacienti
        GROUP BY ockovani
    """)
    if df.empty:
        print("  [covid_vax] zadna data"); return

    order = ["Nevakcinován", "1 dávka", "2 dávky", "3 dávky", "4+ dávky"]
    df["ockovani"] = pd.Categorical(df["ockovani"], categories=order, ordered=True)
    df = df.sort_values("ockovani")
    df["hosp_rate"] = (df["hospitalizace"] / df["pripady"] * 100).round(1)

    labels = df["ockovani"].tolist()
    save("covid_by_vaccination", {
        "labels": labels,
        "datasets": [
            ds("Případy", df["pripady"].tolist(), "blue", "bar"),
            ds("Hospitalizace", df["hospitalizace"].tolist(), "orange", "bar"),
            ds("Úmrtí", df["umrti"].tolist(), "red", "bar"),
        ],
    })
    save("covid_hosp_rate_by_vax", {
        "labels": labels,
        "datasets": [
            ds("Hospitalizační míra (%)", df["hosp_rate"].tolist(), "orange"),
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# ISIN — Infekční nemoci (ÚZIS ČR, CC BY 4.0)
# ─────────────────────────────────────────────────────────────────────────────

ISIN_FILE = DATA_IN / "isin" / "isin_infekcni_nemoci.csv"

# Mapping NUTS3 → human-readable short name (same order as SVG map)
NUTS3_NAMES = {
    "CZ010": "Praha",
    "CZ020": "Středočeský",
    "CZ031": "Jihočeský",
    "CZ032": "Plzeňský",
    "CZ041": "Karlovarský",
    "CZ042": "Ústecký",
    "CZ051": "Liberecký",
    "CZ052": "Královéhradecký",
    "CZ053": "Pardubický",
    "CZ063": "Vysočina",
    "CZ064": "Jihomoravský",
    "CZ071": "Olomoucký",
    "CZ072": "Zlínský",
    "CZ080": "Moravskoslezský",
}


def _load_isin() -> pd.DataFrame:
    if not ISIN_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(ISIN_FILE, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    return df


# ── 13. Top 10 diagnóz — roční počty případů 2018–2025 ───────────────────────
def isin_top_diseases():
    df = _load_isin()
    if df.empty:
        print("  [isin_top] žádná data"); return

    # Top 10 diagnóz dle celkového počtu
    top = (df.groupby("diagnoza_nazev")["pocet_pripadu"]
             .sum()
             .sort_values(ascending=False)
             .head(10)
             .index.tolist())

    by_year = (df[df["diagnoza_nazev"].isin(top)]
               .groupby(["rok", "diagnoza_nazev"])["pocet_pripadu"]
               .sum()
               .unstack(fill_value=0))

    years = [str(y) for y in sorted(by_year.index.tolist())]
    color_cycle = ["blue", "red", "teal", "orange", "green", "purple",
                   "blue", "red", "teal", "orange"]
    datasets = []
    for i, diag in enumerate(top):
        if diag in by_year.columns:
            datasets.append(ds(diag, by_year[diag].tolist(),
                               color_cycle[i % len(color_cycle)], "bar"))

    save("isin_top_diseases", {"labels": years, "datasets": datasets})


# ── 14. Regionální mapa — počet případů per kraj (posledni dostupny rok) ─────
def isin_regional_map():
    df = _load_isin()
    if df.empty:
        print("  [isin_map] žádná data"); return

    last_year = int(df["rok"].max())
    by_region = (df[df["rok"] == last_year]
                 .groupby("kraj_kod")["pocet_pripadu"]
                 .sum()
                 .reset_index())

    # Build dict: NUTS3 code → count
    region_data = {}
    for _, row in by_region.iterrows():
        code = str(row["kraj_kod"]).strip()
        if code in NUTS3_NAMES:
            region_data[code] = int(row["pocet_pripadu"])

    save("isin_regional_map", {
        "year": last_year,
        "regions": region_data,
        "labels": NUTS3_NAMES,
    })


# ── 15. Sezónní trend — měsíční případy vybraných nemocí (2018–) ─────────────
def isin_monthly_trend():
    df = _load_isin()
    if df.empty:
        print("  [isin_monthly] žádná data"); return

    SELECTED = {
        "Plané neštovice [varicella]": "purple",
        "Jiné infekce způsobené salmonelami": "orange",
        "Dávivý kašel [pertussis]": "red",
        "Jiné spirochetové infekce": "green",
    }
    mask = df["diagnoza_nazev"].isin(SELECTED.keys())
    df2 = df[mask].copy()
    df2["ym"] = df2["rok"].astype(str) + "-" + df2["mesic"].astype(str).str.zfill(2)
    by_ym = (df2.groupby(["ym", "diagnoza_nazev"])["pocet_pripadu"]
               .sum()
               .unstack(fill_value=0))
    by_ym = by_ym.sort_index()

    labels = by_ym.index.tolist()
    color_map = SELECTED
    datasets = []
    for diag in SELECTED:
        if diag in by_ym.columns:
            datasets.append(ds(diag, by_ym[diag].tolist(),
                               color_map.get(diag, "blue")))

    save("isin_monthly_trend", {"labels": labels, "datasets": datasets})


# ── 16. Věkové rozložení — případy dle věkové skupiny (celkem 2018–) ──────────
def isin_age_groups():
    df = _load_isin()
    if df.empty:
        print("  [isin_age] žádná data"); return

    # Sort by numeric vek_kod to get correct age order
    by_age = (df.groupby(["vek_kod", "vek_nazev"])["pocet_pripadu"]
                .sum()
                .reset_index()
                .sort_values("vek_kod"))

    save("isin_age_groups", {
        "labels": by_age["vek_nazev"].tolist(),
        "datasets": [ds("Počet případů (2018–)", by_age["pocet_pripadu"].tolist(), "blue", "bar")],
    })


if __name__ == "__main__":
    print("Generuji Chart.js JSON data...")
    covid_cases_weekly()
    covid_hospitalization()
    covid_testing()
    covid_incidence()
    covid_summary()
    print("  --- Influenza ---")
    flu_season_overview()
    flu_weekly_last_season()
    flu_respiratory_all()
    print("  --- Regionální ---")
    flu_regional_overview()
    flu_regional_weekly()
    print("  --- COVID věk & očkování ---")
    covid_by_age()
    covid_by_vaccination()
    print("  --- ISIN infekční nemoci ---")
    isin_top_diseases()
    isin_regional_map()
    isin_monthly_trend()
    isin_age_groups()
    print("Hotovo.")

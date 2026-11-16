"""
ČSÚ — počet obyvatel podle kraje, roku a pohlaví.
Zdroj: data.csu.gov.cz DataStat API, datová sada PORKR01
Licence: CC BY 4.0 podle podmínek ČSÚ; odvozené údaje (incidence) se musí
         označit jako odvozené — viz sources.yaml
Aktualizace: ročně

Proč právě PORKR01: potřebujeme jmenovatele pro přepočet na incidenci (na 100 tis.
obyvatel). Věkově stratifikované sady (OBY02D) mají krásná pětiletá pásma, ale API je
kvůli velikosti nevydá synchronně — musely by se tahat asynchronně. PORKR01 je jeden
GET, malý, a na celkovou incidenci podle kraje stačí.

Věk: sada WGEN01AG1 (počet obyvatel podle jednotek věku, celá ČR, k 31. 12.) je
malá a API ji vydá jedním GET. Jednotky věku jdou sečíst do libovolných skupin,
takže poslouží pro ISIN (pětileté skupiny) i pro týdenní souhrn ÚZIS (jiné
skupiny). Výstup: population_age.csv — jen celá ČR, obě pohlaví dohromady.

Pozn. k metodice: ukazatel je "Počet obyvatel k 31. 12." (koncový stav). Učebnicově
se pro incidenci používá střední stav (k 1. 7.), rozdíl je ale řádu desetin procenta
ročně — pro zobrazení na dashboardu zanedbatelné. Kdyby bylo potřeba přesněji, dá se
střední stav aproximovat průměrem dvou po sobě jdoucích koncových stavů.
"""

import io
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.csu.gov.cz/api/dotaz/v1/data/sady/PORKR01?format=CSV"
AGE_URL = "https://data.csu.gov.cz/api/dotaz/v1/data/sady/WGEN01AG1?format=CSV"

# Nejvyšší věk je v sadě otevřený interval „100 a více“; ukládá se jako 100.
AGE_OPEN_END = 100

# Názvy území v ČSÚ → NUTS3 kódy (stejné kódy, jaké používá ISIN v generate_json.py)
REGION_TO_NUTS3 = {
    "Česko":                "CZ",     # celá republika
    "Hlavní město Praha":   "CZ010",
    "Středočeský kraj":     "CZ020",
    "Jihočeský kraj":       "CZ031",
    "Plzeňský kraj":        "CZ032",
    "Karlovarský kraj":     "CZ041",
    "Ústecký kraj":         "CZ042",
    "Liberecký kraj":       "CZ051",
    "Královéhradecký kraj": "CZ052",
    "Pardubický kraj":      "CZ053",
    "Kraj Vysočina":        "CZ063",
    "Jihomoravský kraj":    "CZ064",
    "Olomoucký kraj":       "CZ071",
    "Zlínský kraj":         "CZ072",
    "Moravskoslezský kraj": "CZ080",
}


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [csu_population] stahuju {URL}")

    resp = requests.get(URL, timeout=60)
    resp.raise_for_status()

    df = pd.read_csv(io.BytesIO(resp.content))
    if df.empty:
        raise ValueError("ČSÚ vrátil prázdný CSV — API nebo datová sada se pravděpodobně změnily")

    df = df.rename(columns={
        "Roky": "rok",
        "ČR, kraje": "kraj_nazev",
        "Pohlaví": "pohlavi",
        "Hodnota": "pocet",
    })

    missing = set(REGION_TO_NUTS3) - set(df["kraj_nazev"].unique())
    if missing:
        raise ValueError(f"V datech ČSÚ chybí očekávaná území: {sorted(missing)}")

    df["kraj_kod"] = df["kraj_nazev"].map(REGION_TO_NUTS3)
    df = df.dropna(subset=["kraj_kod", "pocet"])
    df["pocet"] = df["pocet"].astype(int)

    out = df[["rok", "kraj_kod", "kraj_nazev", "pohlavi", "pocet"]].sort_values(
        ["rok", "kraj_kod", "pohlavi"]
    )

    out_path = output_dir / "population.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(f"  [csu_population] {len(out):,} radku, roky {out['rok'].min()}–{out['rok'].max()} → {out_path}")

    # Věk je doplněk pro dva bloky jedné stránky. Jeho výpadek nesmí vrátit chybu:
    # ta by v run_all.py zastavila obnovu všech dat, i těch, která na věku nestojí.
    # Zůstane poslední podařený soubor a přepočty na věk se dál počítají z něj.
    try:
        return [str(out_path), str(download_age(output_dir))]
    except Exception as e:
        print(f"  [csu_population] věk se nepodařilo stáhnout ({type(e).__name__}: {e}) — pokračuji bez něj")
        return [str(out_path)]


def parse_age(content: bytes) -> pd.DataFrame:
    """CSV sady WGEN01AG1 → rok, vek (0–100, kde 100 = 100 a více), pocet (obě pohlaví)."""
    df = pd.read_csv(io.BytesIO(content))
    need = {"Ukazatel", "Věk (roky)", "Území", "Roky", "Hodnota"}
    if df.empty or need - set(df.columns):
        raise ValueError(f"ČSÚ WGEN01AG1: chybí sloupce {sorted(need - set(df.columns))} nebo je sada prázdná")
    df = df[df["Území"] == "Česko"]
    age = df["Věk (roky)"].astype(str).str.extract(r"^(\d+)")[0]
    if age.isna().any():
        raise ValueError(f"ČSÚ WGEN01AG1: nečitelný věk {df.loc[age.isna(), 'Věk (roky)'].unique()[:3]}")
    df = df.assign(vek=age.astype(int), rok=df["Roky"].astype(int))
    out = df.groupby(["rok", "vek"], as_index=False)["Hodnota"].sum().rename(columns={"Hodnota": "pocet"})
    out["pocet"] = out["pocet"].round().astype(int)
    # Součet mužů a žen: každý rok musí mít oba ukazatele a všechny jednotky věku.
    per_year = df.groupby("rok")["Ukazatel"].nunique()
    if (per_year != 2).any() or (out.groupby("rok")["vek"].nunique() != AGE_OPEN_END + 1).any():
        raise ValueError("ČSÚ WGEN01AG1: některý rok nemá obě pohlaví nebo všechny jednotky věku 0–100")
    return out.sort_values(["rok", "vek"])


def download_age(output_dir: Path) -> Path:
    print(f"  [csu_population] stahuju {AGE_URL}")
    resp = requests.get(AGE_URL, timeout=60)
    resp.raise_for_status()
    out = parse_age(resp.content)
    path = output_dir / "population_age.csv"
    out.to_csv(path, index=False, encoding="utf-8")
    print(f"  [csu_population] věk: {len(out):,} radku, roky {out['rok'].min()}–{out['rok'].max()} → {path}")
    return path


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "csu")

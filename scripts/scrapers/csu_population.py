"""
ČSÚ — počet obyvatel podle kraje, roku a pohlaví.
Zdroj: data.csu.gov.cz DataStat API, datová sada PORKR01
Licence: otevřená data ČSÚ
Aktualizace: ročně

Proč právě PORKR01: potřebujeme jmenovatele pro přepočet na incidenci (na 100 tis.
obyvatel). Věkově stratifikované sady (OBY02D) mají krásná pětiletá pásma, ale API je
kvůli velikosti nevydá synchronně — musely by se tahat asynchronně. PORKR01 je jeden
GET, malý, a na celkovou incidenci podle kraje stačí. Věkovou stratifikaci doplníme,
až ji bude reálně potřeba některý graf.

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
    return [str(out_path)]


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "csu")

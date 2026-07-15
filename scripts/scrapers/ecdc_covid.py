"""
ECDC COVID-19 scraper — filtruje data pro Českou republiku
Zdroj: opendata.ecdc.europa.eu
Licence: ECDC copyright, volné použití s uvedením zdroje
Aktualizace: týdenně (data končí říjen 2022, ECDC přestal publikovat po OWID)
"""

import requests
import pandas as pd
from pathlib import Path

URL = (
    "https://opendata.ecdc.europa.eu"
    "/covid19/nationalcasedeath_eueea_daily_ei/csv/data.csv"
)

COUNTRY_CODE = "CZ"


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ecdc_covid] stahuju {URL}")

    resp = requests.get(URL, timeout=60)
    resp.raise_for_status()

    df = pd.read_csv(pd.io.common.BytesIO(resp.content))

    # Filtr na CR
    df_cz = df[df["geoId"] == COUNTRY_CODE].copy()

    # Datum ze sloupcu den/mesic/rok
    df_cz["datum"] = pd.to_datetime(
        df_cz[["year", "month", "day"]].rename(columns={"year": "year", "month": "month", "day": "day"})
    ).dt.date

    df_cz = df_cz[["datum", "cases", "deaths", "popData2020"]].sort_values("datum")
    df_cz.columns = ["datum", "nove_pripady", "nove_umrti", "populace"]

    out_path = output_dir / "ecdc_covid_cz.csv"
    df_cz.to_csv(out_path, index=False, encoding="utf-8")
    print(f"  [ecdc_covid] {len(df_cz):,} radku (CZ) → {out_path}")
    return [str(out_path)]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    download(root / "data" / "ecdc")

"""
MZCR COVID-19 scraper
Zdroj: onemocneni-aktualne.mzcr.cz/api/v2/covid-19
Licence: otevřená data MZČR
Aktualizace: denně
"""

import requests
import pandas as pd
from pathlib import Path

BASE_URL = "https://onemocneni-aktualne.mzcr.cz/api/v2/covid-19"

DATASETS = {
    "covid_pripady":      "nakazeni-vyleceni-umrti-testy.csv",
    "covid_hospitalizace": "hospitalizace.csv",
    "covid_testy":        "testy-pcr-antigenni.csv",
    "covid_ockovani":     "ockovani.csv",
    "covid_incidence":    "incidence-7-14-cr.csv",
}


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    for name, filename in DATASETS.items():
        url = f"{BASE_URL}/{filename}"
        print(f"  [{name}] stahuju {url}")

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        # MZCR CSV maji BOM, pandas si s tim poradi automaticky
        df = pd.read_csv(
            pd.io.common.BytesIO(resp.content),
            encoding="utf-8-sig",
        )

        # Sjednotime nazev datumoveho sloupce
        if "datum" in df.columns:
            df["datum"] = pd.to_datetime(df["datum"]).dt.date

        out_path = output_dir / f"{name}.csv"
        df.to_csv(out_path, index=False, encoding="utf-8")
        print(f"  [{name}] {len(df):,} radku → {out_path}")
        downloaded.append(str(out_path))

    return downloaded


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    download(root / "data" / "mzcr")

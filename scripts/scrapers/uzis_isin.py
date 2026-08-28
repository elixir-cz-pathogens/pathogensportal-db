"""
Stahuje ISIN data (infekční nemoci) z ÚZIS ČR — otevřená data CC BY 4.0.
URL: https://datanzis.uzis.gov.cz/data/NR-27-ISIN/NR-27-01/Otevrena-data-NR-27-01-infekcni-nemoci.csv
Výstup: data/isin/isin_infekcni_nemoci.csv
"""

import urllib.request
from pathlib import Path

URL = (
    "https://datanzis.uzis.gov.cz/data/NR-27-ISIN/NR-27-01/"
    "Otevrena-data-NR-27-01-infekcni-nemoci.csv"
)


def download(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "isin_infekcni_nemoci.csv"
    print(f"  Stahuji {URL}")
    urllib.request.urlretrieve(URL, dest)
    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"  OK → {dest} ({size_mb:.1f} MB)")
    return [dest]


if __name__ == "__main__":
    download(Path(__file__).resolve().parents[2] / "data" / "isin")

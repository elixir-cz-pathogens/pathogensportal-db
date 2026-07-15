"""
Spustí všechny scrapery a uloží data do data/
Použití: python scripts/run_all.py
"""

import sys
import time
from pathlib import Path

# Přidej scripts/ do Python path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers import mzcr_covid, ecdc_covid, szu_influenza, uzis_isin

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def run():
    jobs = [
        ("MZCR COVID-19",        mzcr_covid.download,    DATA_ROOT / "mzcr"),
        ("ECDC COVID-19 (CZ)",   ecdc_covid.download,    DATA_ROOT / "ecdc"),
        ("SZÚ chřipka (hist.)",  szu_influenza.download, DATA_ROOT / "szu"),
        ("ÚZIS ISIN inf. nem.",  uzis_isin.download,     DATA_ROOT / "isin"),
    ]

    all_files = []
    for label, fn, out_dir in jobs:
        print(f"\n{'='*50}")
        print(f"  {label}")
        print(f"{'='*50}")
        t0 = time.time()
        try:
            files = fn(out_dir)
            all_files.extend(files)
            print(f"  OK — {len(files)} souboru za {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  CHYBA: {e}")

    print(f"\n{'='*50}")
    print(f"  Hotovo — {len(all_files)} CSV souboru v {DATA_ROOT}")
    for f in all_files:
        print(f"    {f}")


if __name__ == "__main__":
    run()

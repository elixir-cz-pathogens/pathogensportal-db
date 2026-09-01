"""
Spustí všechny scrapery a uloží data do data/
Použití: python scripts/run_all.py
Exit kód: 0 pokud všechny zdroje uspěly, 1 pokud alespoň jeden selhal
(důležité pro cron/CI — bez toho selhání zdroje projde bez povšimnutí).
"""

import os
import sys
import time
from pathlib import Path

# Přidej scripts/ do Python path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers import mzcr_covid, ecdc_covid, szu_influenza, uzis_isin
from snapshot import snapshot

DATA_ROOT = Path(os.getenv("DATA_DIR") or Path(__file__).resolve().parents[1] / "data")


def run() -> int:
    jobs = [
        ("MZCR COVID-19",         mzcr_covid.download,         DATA_ROOT / "mzcr"),
        ("ECDC COVID-19 (CZ)",    ecdc_covid.download,         DATA_ROOT / "ecdc"),
        ("SZÚ chřipka (hist.)",   szu_influenza.download,      DATA_ROOT / "szu"),
        ("SZÚ chřipka (aktuál.)", szu_influenza.download_current, DATA_ROOT / "szu"),
        ("ÚZIS ISIN inf. nem.",   uzis_isin.download,          DATA_ROOT / "isin"),
    ]

    all_files = []
    failures = []
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
            failures.append((label, e))

    print(f"\n{'='*50}")
    print(f"  Hotovo — {len(all_files)} CSV souboru v {DATA_ROOT}")
    for f in all_files:
        print(f"    {f}")

    # Snapshot i při částečném selhání — co se stáhlo, má smysl uchovat.
    if all_files:
        result = snapshot(all_files, DATA_ROOT)
        print(f"\n  Snapshot: {len(result['new'])} nových, "
              f"{len(result['unchanged'])} beze změny (přeskočeno)")
        for rel in result["new"]:
            print(f"    + {rel}")

    if failures:
        print(f"\n  SELHALO {len(failures)}/{len(jobs)} zdrojů:")
        for label, e in failures:
            print(f"    - {label}: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())

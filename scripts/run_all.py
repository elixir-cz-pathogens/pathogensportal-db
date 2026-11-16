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

from scrapers import (mzcr_covid, ecdc_covid, ecdc_erviss, ecdc_erviss_snapshots, ecdc_respicast,
                      ena_czech, ncbi_pathogen, nextstrain_czech,
                      szu_influenza, szu_weekly, uzis_isin, uzis_isin_weekly, uzis_registries,
                      csu_population,
                      who_flu)
from snapshot import snapshot

DATA_ROOT = Path(os.getenv("DATA_DIR") or Path(__file__).resolve().parents[1] / "data")


def run() -> int:
    jobs = [
        ("MZCR COVID-19",         mzcr_covid.download,         DATA_ROOT / "mzcr"),
        ("ECDC COVID-19 (CZ)",    ecdc_covid.download,         DATA_ROOT / "ecdc"),
        ("SZÚ chřipka (hist.)",   szu_influenza.download,      DATA_ROOT / "szu"),
        ("SZÚ chřipka (aktuál.)", szu_influenza.download_current, DATA_ROOT / "szu"),
        ("SZÚ týdenní PDF",       szu_weekly.download,          DATA_ROOT / "szu"),
        ("ÚZIS ISIN inf. nem.",   uzis_isin.download,          DATA_ROOT / "isin"),
        ("ÚZIS registry RPN+TBC", uzis_registries.download,    DATA_ROOT / "uzis"),
        ("ČSÚ populace",          csu_population.download,     DATA_ROOT / "csu"),
        ("WHO FluNet + FluID",    who_flu.download,            DATA_ROOT / "who"),
        ("ECDC ERVISS (CZ)",      ecdc_erviss.download,        DATA_ROOT / "ecdc"),
        ("ECDC RespiCast (CZ)",   ecdc_respicast.download,     DATA_ROOT / "ecdc"),
    ]

    # Vedlejší zdroje: tři genomické archivy a týdenní souhrn ÚZIS. Nezávisí na
    # nich žádný jiný graf než jejich vlastní bloky.
    # Běží ve stejné smyčce jako povinné zdroje (ať se dostanou do snapshotu
    # a do metadat o zdrojích), ale jejich selhání nevrací exit 1 — ten by
    # v kontejneru zastavil celý řetěz a portál by kvůli výpadku ENA nebo NCBI
    # přišel o obnovu chřipky i covidu. compute_genomics.py pak JSON nepřepíše
    # (viz save() tamtéž), takže datum aktualizace na stránce zůstane pravdivé.
    side_jobs = [
        ("ENA sekvence (CZ)",     ena_czech.download,          DATA_ROOT / "ena"),
        ("Nextstrain stromy (CZ)", nextstrain_czech.download,  DATA_ROOT / "nextstrain"),
        ("NCBI rezistence (CZ)",  ncbi_pathogen.download,      DATA_ROOT / "ncbi"),
        # Týdenní souhrn ÚZIS leží na sdíleném úložišti, ne v katalogu otevřených
        # dat: odkaz se může změnit bez ohlášení. Stojí na něm jediný blok jedné
        # stránky (compute_isin.py), a ten při výpadku zůstane u posledního stavu.
        ("ÚZIS ISIN týdenní souhrn", uzis_isin_weekly.download, DATA_ROOT / "isin"),
    ]
    side_labels = {label for label, _, _ in side_jobs}

    all_files = []
    failures = []
    side_failures = []
    for label, fn, out_dir in jobs + side_jobs:
        print(f"\n{'='*50}")
        print(f"  {label}")
        print(f"{'='*50}")
        t0 = time.time()
        try:
            files = fn(out_dir)
            all_files.extend(files)
            print(f"  OK — {len(files)} souboru za {time.time()-t0:.1f}s")
        except Exception as e:
            if label in side_labels:
                print(f"  CHYBA (ostatní data tím nejsou dotčena): {type(e).__name__}: {e}")
                side_failures.append((label, e))
            else:
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

    # Volitelné zdroje: slouží jen analytice (nowcast), ne žádnému grafu přímo.
    # Jejich selhání se do `failures` nepočítá — exit 1 by zastavil celý řetěz
    # v kontejneru a portál by přišel o obnovu všech dat kvůli vedlejší věci.
    optional = [
        ("ECDC ERVISS snapshoty (nowcast)", ecdc_erviss_snapshots.download, DATA_ROOT / "ecdc"),
    ]
    for label, fn, out_dir in optional:
        print(f"\n{'='*50}")
        print(f"  {label} — volitelné")
        print(f"{'='*50}")
        try:
            fn(out_dir)
        except Exception as e:
            print(f"  CHYBA (ostatní data tím nejsou dotčena): {type(e).__name__}: {e}")

    # Metadata o zdrojích až po snapshotu — čtou z manifestu datum posledního
    # stažení. Selhání se nepřipisuje ke `failures`: bez popisku zdroje je
    # pipeline ochuzená, ale funkční, a shazovat kvůli tomu celý běh by
    # znamenalo přijít i o data, která se stáhla v pořádku.
    print(f"\n{'='*50}")
    print("  Metadata o zdrojích")
    print(f"{'='*50}")
    try:
        from source_metadata import collect, write
        meta = collect()
        path = write(meta)
        broken = [sid for sid, s in meta["sources"].items() if s.get("errors")]
        print(f"  {len(meta['sources']) - len(broken)}/{len(meta['sources'])} zdrojů → {path}")
    except Exception as e:
        print(f"  CHYBA (data tím nejsou dotčena): {type(e).__name__}: {e}")

    if side_failures:
        print(f"\n  Vedlejší zdroje — selhalo {len(side_failures)}/{len(side_jobs)} "
              f"(běh pokračuje, jejich stránky zůstanou u poslední podařené verze):")
        for label, e in side_failures:
            print(f"    - {label}: {e}")

    if failures:
        print(f"\n  SELHALO {len(failures)}/{len(jobs)} zdrojů:")
        for label, e in failures:
            print(f"    - {label}: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())

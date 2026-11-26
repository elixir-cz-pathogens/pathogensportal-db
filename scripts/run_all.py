"""
Spustí všechny scrapery a uloží data do data/
Použití: python scripts/run_all.py
Exit kód: 0 pokud všechny zdroje uspěly, 1 pokud alespoň jeden selhal
(důležité pro cron/CI — bez toho selhání zdroje projde bez povšimnutí).
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Přidej scripts/ do Python path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers import (mzcr_covid, ecdc_covid, ecdc_erviss, ecdc_erviss_snapshots, ecdc_respicast,
                      ena_czech, ncbi_pathogen, nextstrain_czech,
                      szu_influenza, szu_weekly, uzis_isin, uzis_isin_weekly, uzis_registries,
                      csu_population,
                      who_flu)
from snapshot import snapshot
import last_checked

DATA_ROOT = Path(os.getenv("DATA_DIR") or Path(__file__).resolve().parents[1] / "data")


def run() -> int:
    started = datetime.now(timezone.utc)
    # Čtvrtá položka: id zdrojů ze sources.yaml, které úloha ověřuje. Když
    # doběhne bez chyby, platí zdroj pro tento běh za ověřený (last_checked.py
    # → datum „naposledy ověřeno“ u panelu). Prázdná = úloha se zdroje neptá:
    # historické sezóny SZÚ se stahují jednou a pak leží na disku.
    jobs = [
        ("MZCR COVID-19",         mzcr_covid.download,         DATA_ROOT / "mzcr", ("mzcr-covid",)),
        ("ECDC COVID-19 (CZ)",    ecdc_covid.download,         DATA_ROOT / "ecdc", ("ecdc-covid",)),
        ("SZÚ chřipka (hist.)",   szu_influenza.download,      DATA_ROOT / "szu",  ()),
        ("SZÚ chřipka (aktuál.)", szu_influenza.download_current, DATA_ROOT / "szu", ("szu-respiratory",)),
        ("SZÚ týdenní PDF",       szu_weekly.download,          DATA_ROOT / "szu", ("szu-respiratory",)),
        ("ÚZIS ISIN inf. nem.",   uzis_isin.download,          DATA_ROOT / "isin", ("uzis-isin",)),
        ("ÚZIS registry RPN+TBC", uzis_registries.download,    DATA_ROOT / "uzis", ("uzis-rpn", "uzis-rtbc")),
        ("ČSÚ populace",          csu_population.download,     DATA_ROOT / "csu",  ("csu-population",)),
        ("WHO FluNet + FluID",    who_flu.download,            DATA_ROOT / "who",  ("who-fluid-flunet",)),
        ("ECDC ERVISS (CZ)",      ecdc_erviss.download,        DATA_ROOT / "ecdc", ("ecdc-erviss",)),
        ("ECDC RespiCast (CZ)",   ecdc_respicast.download,     DATA_ROOT / "ecdc", ("ecdc-respicast",)),
    ]

    # Vedlejší zdroje: tři genomické archivy a týdenní souhrn ÚZIS. Nezávisí na
    # nich žádný jiný graf než jejich vlastní bloky.
    # Běží ve stejné smyčce jako povinné zdroje (ať se dostanou do snapshotu
    # a do metadat o zdrojích), ale jejich selhání nevrací exit 1 — ten by
    # v kontejneru zastavil celý řetěz a portál by kvůli výpadku ENA nebo NCBI
    # přišel o obnovu chřipky i covidu. compute_genomics.py pak JSON nepřepíše
    # (viz save() tamtéž), takže datum aktualizace na stránce zůstane pravdivé.
    side_jobs = [
        ("ENA sekvence (CZ)",     ena_czech.download,          DATA_ROOT / "ena", ("ena",)),
        ("Nextstrain stromy (CZ)", nextstrain_czech.download,  DATA_ROOT / "nextstrain", ("nextstrain",)),
        ("NCBI rezistence (CZ)",  ncbi_pathogen.download,      DATA_ROOT / "ncbi", ("ncbi-pathogen-detection",)),
        # Týdenní souhrn ÚZIS leží na sdíleném úložišti, ne v katalogu otevřených
        # dat: odkaz se může změnit bez ohlášení. Stojí na něm jediný blok jedné
        # stránky (compute_isin.py), a ten při výpadku zůstane u posledního stavu.
        ("ÚZIS ISIN týdenní souhrn", uzis_isin_weekly.download, DATA_ROOT / "isin", ("uzis-isin-weekly",)),
    ]
    side_labels = {label for label, *_ in side_jobs}

    all_files = []
    failures = []
    side_failures = []
    # Zdroj je ověřený, jen když se ho zeptaly VŠECHNY jeho úlohy (SZÚ má dvě).
    asked, not_asked = set(), set()
    for label, fn, out_dir, source_ids in jobs + side_jobs:
        print(f"\n{'='*50}")
        print(f"  {label}")
        print(f"{'='*50}")
        t0 = time.time()
        try:
            files = fn(out_dir)
            all_files.extend(files)
            print(f"  OK — {len(files)} souboru za {time.time()-t0:.1f}s")
            # Scraper smí doběhnout i bez dotazu na zdroj (soubor z cache,
            # doplněk, který se nestáhl) — pak si nastaví source_checked = False.
            (asked if getattr(fn, "source_checked", True) else not_asked).update(source_ids)
        except Exception as e:
            not_asked.update(source_ids)
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

    # Co se v tomto běhu opravdu ověřilo — i při částečném selhání: generátory
    # pak sice v kontejneru neběží, ale záznam o zdroji je pravdivý tak jako tak.
    # Zápis nesmí shodit běh ze stejného důvodu jako metadata výš.
    try:
        last_checked.record_sources(asked - not_asked, started, DATA_ROOT / "meta" / "checks.json")
        print(f"\n  Ověřeno u zdroje: {len(asked - not_asked)} zdrojů"
              + (f"; bez ověření: {', '.join(sorted(not_asked))}" if not_asked else ""))
    except Exception as e:
        print(f"\n  CHYBA při zápisu ověření zdrojů (data tím nejsou dotčena): {type(e).__name__}: {e}")

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

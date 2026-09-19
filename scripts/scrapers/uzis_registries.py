"""
ÚZIS — Registr pohlavních nemocí (RPN) a Registr tuberkulózy (RTBC), otevřená data.
Zdroj: datanzis.uzis.gov.cz, sady NR-29-01 a NR-30-01
Licence: volný přístup (data.gov.cz/podmínky-užití/volný-přístup)
Aktualizace: ročně (soubory z února 2026 nesou rok 2025, resp. 2024)

Proč zvlášť, když máme ISIN: tyhle nemoci v ISIN NEJSOU. Syfilis, kapavka
a tuberkulóza mají vlastní povinné registry, takže stránka „Pohlavně přenosné“
postavená jen nad ISIN ukazovala chlamydie a trichomoniázu a dvě nejdůležitější
pohlavní nemoci na ní chyběly úplně.

  RPN   A50–A53 syfilis, A54 kapavka, A55 venerický lymfogranulom (A57 měkký
        vřed je v definici registru, ale v datech se nevyskytuje); od 1994;
        okres bydliště × pohlaví × věk × diagnóza × rok
  RTBC  A15–A19; od 2000; rok a čtvrtletí incidence × okres × kraj dispenzarizace
        × věk × rodná země (příznak `CZ` + region OSN M49)

Oba soubory jsou malé (2 MB, 0,8 MB) a přepisují se celé — žádná cache.

Výstup: uzis_pohlavni_nemoci.csv, uzis_tuberkuloza.csv (sloupce beze změny)
"""

import io
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://datanzis.uzis.gov.cz/data"
DATASETS = {
    "uzis_pohlavni_nemoci.csv": {
        "url": f"{BASE_URL}/NR-29-RPN/NR-29-01/Otevrena-data-NR-29-01-pohlavni-nemoci.csv",
        "columns": {"rok", "okres_kod", "pohlavi", "vek_nazev", "diagnoza_kod",
                    "diagnoza_nazev", "pocet_pripadu"},
        "year": "rok",
    },
    "uzis_tuberkuloza.csv": {
        "url": f"{BASE_URL}/NR-30-RTBC/NR-30-01/Otevrena-data-NR-30-01-tuberkuloza-epidemiologie.csv",
        "columns": {"rok_incidence", "kvartal_incidence", "okres_kod", "kraj_kod", "CZ",
                    "vek_nazev", "pripady"},
        "year": "rok_incidence",
    },
}
# Registry vycházejí jednou ročně se zhruba ročním zpožděním. Starší poslední rok
# znamená, že ÚZIS přestal sadu aktualizovat — a bez téhle kontroly by to nikdo nezjistil.
MAX_LAG_YEARS = 3


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for name, spec in DATASETS.items():
        print(f"  [uzis_registries] stahuju {spec['url']}")
        resp = requests.get(spec["url"], timeout=120)
        resp.raise_for_status()

        df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig")
        missing = spec["columns"] - set(df.columns)
        if missing:
            raise ValueError(f"{name}: chybí očekávané sloupce {sorted(missing)} — ÚZIS změnil formát")
        if df.empty:
            raise ValueError(f"{name}: prázdný soubor")

        last = int(df[spec["year"]].max())
        if last < pd.Timestamp.today().year - MAX_LAG_YEARS:
            raise ValueError(f"{name}: poslední rok je {last} — sada přestala přibývat")

        path = output_dir / name
        path.write_bytes(resp.content)
        print(f"  [uzis_registries] {len(df):,} radku, {int(df[spec['year']].min())}–{last} → {path}")
        files.append(str(path))
    return files


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "uzis")

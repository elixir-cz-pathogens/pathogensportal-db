"""
ECDC ERVISS — týdenní respirační surveillance EU/EEA, řádky za ČR.
Zdroj: github.com/EU-ECDC/Respiratory_viruses_weekly_data (CSV za ERVISS, erviss.org)
Licence: otevřená data ECDC s povinným uvedením zdroje — přesné znění podmínek
         ověřit, než se čísla objeví na portálu
Aktualizace: týdně (čtvrtek/pátek), celoročně

Nahrazuje mrtvý ecdc_covid jako živé spojení na ECDC. Proti WHO FluID (stejný
ukazatel, delší historie — viz who_flu.py) má ERVISS dvě výhody: je čerstvější
(FluID se mimo sezónu zpožďuje o měsíce) a míry dává rovnou na 100 tis. po
věkových skupinách. Historie za ČR ale začíná až 2022-W25, takže na sezónní
prahy sama nestačí — slouží jako aktuální týden k historii z FluID.

Výstup:
  erviss_ili_ari_cz.csv     — tyden_iso, ukazatel (ILI/ARI), vek, mira_na_100k
  erviss_nonsentinel_cz.csv — tyden_iso, patogen, typ, subtyp, ukazatel
                              (detections/tests), vek, hodnota
"""

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://raw.githubusercontent.com/EU-ECDC/Respiratory_viruses_weekly_data/main/data"
COUNTRY = "Czechia"
MAX_AGE_DAYS = 45

INDICATORS = {"ILIconsultationrate": "ILI", "ARIconsultationrate": "ARI"}


def _fetch(filename: str, required: set[str]) -> pd.DataFrame:
    url = f"{BASE_URL}/{filename}"
    print(f"  [ecdc_erviss] stahuju {url}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    df = pd.read_csv(io.BytesIO(resp.content))
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{filename}: chybí očekávané sloupce {sorted(missing)} — ECDC změnilo formát")

    df = df[df["countryname"] == COUNTRY].copy()
    if df.empty:
        raise ValueError(f"{filename}: filtr na {COUNTRY} nevrátil žádné řádky")

    # "2026-W36" → pondělí toho ISO týdne; jen kvůli kontrole čerstvosti
    newest = max(date.fromisocalendar(int(w[:4]), int(w[6:]), 1) for w in df["yearweek"].unique())
    if newest < date.today() - timedelta(days=MAX_AGE_DAYS):
        raise ValueError(f"{filename}: poslední týden je {newest} — zdroj přestal přibývat")
    return df


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    rates = _fetch("ILIARIRates.csv", {"countryname", "yearweek", "indicator", "age", "value"})
    unknown = set(rates["indicator"].unique()) - set(INDICATORS)
    if unknown:
        raise ValueError(f"ILIARIRates.csv: neznámé ukazatele {sorted(unknown)}")
    rates["indicator"] = rates["indicator"].map(INDICATORS)
    rates = rates.rename(columns={
        "yearweek": "tyden_iso", "indicator": "ukazatel", "age": "vek", "value": "mira_na_100k",
    })[["tyden_iso", "ukazatel", "vek", "mira_na_100k"]].sort_values(["tyden_iso", "ukazatel", "vek"])
    rates_path = output_dir / "erviss_ili_ari_cz.csv"
    rates.to_csv(rates_path, index=False, encoding="utf-8")
    print(f"  [ecdc_erviss] ILI/ARI {len(rates):,} radku, "
          f"{rates['tyden_iso'].min()} – {rates['tyden_iso'].max()} → {rates_path}")

    lab = _fetch("nonSentinelTestsDetections.csv",
                 {"countryname", "yearweek", "pathogen", "pathogentype", "pathogensubtype",
                  "indicator", "age", "value"})
    lab = lab.rename(columns={
        "yearweek": "tyden_iso", "pathogen": "patogen", "pathogentype": "typ",
        "pathogensubtype": "subtyp", "indicator": "ukazatel", "age": "vek", "value": "hodnota",
    })[["tyden_iso", "patogen", "typ", "subtyp", "ukazatel", "vek", "hodnota"]].sort_values(
        ["tyden_iso", "patogen", "typ", "subtyp", "ukazatel"]
    )
    lab_path = output_dir / "erviss_nonsentinel_cz.csv"
    lab.to_csv(lab_path, index=False, encoding="utf-8")
    print(f"  [ecdc_erviss] nonsentinel {len(lab):,} radku, "
          f"{lab['tyden_iso'].min()} – {lab['tyden_iso'].max()} → {lab_path}")

    return [str(rates_path), str(lab_path)]


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "ecdc")

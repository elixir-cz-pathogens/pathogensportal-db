"""
WHO FluNet + FluID — týdenní chřipková surveillance za ČR.
Zdroj: xMart API WHO (xmart-api-public.who.int/FLUMART), jeden GET na dataset
Licence: otevřená data WHO s povinným uvedením zdroje — přesné znění podmínek
         ověřit, než se čísla objeví na portálu (patří do poznámky pod grafem)
Aktualizace: týdně; FluID mívá proti FluNet zpoždění několik týdnů

Proč to bereme, když máme SZÚ PDF:

  FluNet (VIW_FNT)     — laboratorní záchyty po týdnech od 1997. Řádky NONSENTINEL
                         jsou tatáž čísla jako týdenní PDF SZÚ (ověřeno na týdnech
                         2024/25 a 2025/26: Influenza B i RSV na kus stejné), ale
                         navíc nesou SPEC_PROCESSED_NB — počet vyšetřených vzorků,
                         tedy jmenovatele pozitivity, který v PDF podle typu viru
                         chybí. Zároveň vrací týdenní průběh sezón 2022/23 a 2023/24,
                         jejichž PDF už SZÚ online nemá.
  FluID (VIW_FID_EPI)  — ILI a ARI případy + pokrytá populace po týdnech, souvisle
                         od 2009/10. Míra na 100 tis. je srovnatelná napříč roky
                         (na rozdíl od počtu záchytů, který roste s objemem
                         testování) — vstup pro sezónní prahy MEM.

Pozor na ORIGIN_SOURCE ve FluNet: SENTINEL a NONSENTINEL jsou dva různé systémy
hlášené za stejný týden (sentinel ~50 vzorků, nonsentinel ~2 000). Nesčítat
naslepo; starší řádky mají NOTDEFINED. Sloupec necháváme v datech, rozhodnutí
patří až tomu, kdo s řadou počítá.

Výstup:
  who_flunet_cz.csv — rok, tyden, tyden_od, zdroj, vysetreno, inf_a, inf_b, inf_celkem,
                      a_h1n1pdm, a_h3, a_nesubtyp, rsv
  who_fluid_cz.csv  — rok, tyden, tyden_od, vek, ili_pripady, ili_populace,
                      ari_pripady, ari_populace
"""

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://xmart-api-public.who.int/FLUMART"
COUNTRY_CODE = "CZE"

# Starší než tohle = zdroj přestal přibývat a nikdo by si toho jinak nevšiml.
# FluID se v létě zpožďuje o měsíce (mimo sezónu se hlásí řídce), proto volnější mez.
MAX_AGE_DAYS = {"VIW_FNT": 45, "VIW_FID_EPI": 180}

FLUNET_COLS = {
    "ISO_YEAR": "rok",
    "ISO_WEEK": "tyden",
    "ISO_WEEKSTARTDATE": "tyden_od",
    "ORIGIN_SOURCE": "zdroj",
    "SPEC_PROCESSED_NB": "vysetreno",
    "INF_A": "inf_a",
    "INF_B": "inf_b",
    "INF_ALL": "inf_celkem",
    "AH1N12009": "a_h1n1pdm",
    "AH3": "a_h3",
    "ANOTSUBTYPED": "a_nesubtyp",
    "RSV": "rsv",
}

FLUID_COLS = {
    "ISO_YEAR": "rok",
    "ISO_WEEK": "tyden",
    "ISO_WEEKSTARTDATE": "tyden_od",
    "AGEGROUP_CODE": "vek",
    "ILI_CASE": "ili_pripady",
    "ILI_POP_COV": "ili_populace",
    "ARI_CASE": "ari_pripady",
    "ARI_POP_COV": "ari_populace",
}


def _fetch(view: str, columns: dict[str, str]) -> pd.DataFrame:
    url = f"{BASE_URL}/{view}"
    print(f"  [who_flu] stahuju {url} ({COUNTRY_CODE})")
    resp = requests.get(
        url,
        params={"$filter": f"COUNTRY_CODE eq '{COUNTRY_CODE}'", "$format": "csv"},
        timeout=180,
    )
    resp.raise_for_status()

    df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig", low_memory=False)
    if df.empty:
        raise ValueError(f"{view}: filtr na {COUNTRY_CODE} nevrátil žádné řádky")

    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{view}: chybí očekávané sloupce {sorted(missing)} — WHO změnila formát")

    df = df[list(columns)].rename(columns=columns)
    df["tyden_od"] = pd.to_datetime(df["tyden_od"]).dt.date

    newest = df["tyden_od"].max()
    if newest < date.today() - timedelta(days=MAX_AGE_DAYS[view]):
        raise ValueError(f"{view}: poslední týden je {newest} — zdroj přestal přibývat")

    counts = [c for c in df.columns if c not in ("rok", "tyden", "tyden_od", "zdroj", "vek")]
    df[counts] = df[counts].astype("Int64")  # prázdné = nehlášeno, ne nula
    return df


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    flunet = _fetch("VIW_FNT", FLUNET_COLS).sort_values(["rok", "tyden", "zdroj"])
    flunet_path = output_dir / "who_flunet_cz.csv"
    flunet.to_csv(flunet_path, index=False, encoding="utf-8")
    print(f"  [who_flu] FluNet {len(flunet):,} radku, "
          f"{flunet['tyden_od'].min()} – {flunet['tyden_od'].max()} → {flunet_path}")

    fluid = _fetch("VIW_FID_EPI", FLUID_COLS).sort_values(["rok", "tyden", "vek"])
    fluid_path = output_dir / "who_fluid_cz.csv"
    fluid.to_csv(fluid_path, index=False, encoding="utf-8")
    print(f"  [who_flu] FluID {len(fluid):,} radku, "
          f"{fluid['tyden_od'].min()} – {fluid['tyden_od'].max()} → {fluid_path}")

    return [str(flunet_path), str(fluid_path)]


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "who")

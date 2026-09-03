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
    # Náhrada za nereprodukovatelnou covid.db (pacientský linelist neznámého
    # původu): demografie a vakcinační status jdou plně z otevřených dat.
    "covid_umrti":        "umrti.csv",                    # úmrtí s věkem, od 3/2020
    "covid_vax_pozitivni":     "ockovani-pozitivni.csv",       # případy dle očkování, od 1/2021
    "covid_vax_hospitalizace": "ockovani-hospitalizace.csv",   # hospitalizace dle očkování
}

# `osoby.csv` (každý případ s věkem a krajem) má ~330 MB / ~5 mil. řádků —
# surový soubor se neukládá ani nesnapshotuje, streamem se agreguje na
# kompaktní tabulku rok × měsíc × věk × kraj (~3 MB). Chybějící věk se
# NEzahazuje — drží se jako samostatný řádek s vek = -1, ať jde podíl
# nevyplněných uvést na portálu (dnes ~0,5 %).
OSOBY_URL = f"{BASE_URL}/osoby.csv"


def _download_osoby_aggregate(output_dir: Path) -> str:
    import tempfile, os
    print(f"  [covid_osoby] stahuju {OSOBY_URL} (velký soubor, agreguje se streamem)")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        with requests.get(OSOBY_URL, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)
        tmp_path = tmp.name

    try:
        parts = []
        for ch in pd.read_csv(tmp_path, usecols=["datum", "vek", "kraj_nuts_kod"],
                              encoding="utf-8-sig", chunksize=1_000_000):
            ch["datum"] = pd.to_datetime(ch["datum"])
            ch["rok"], ch["mesic"] = ch.datum.dt.year, ch.datum.dt.month
            ch["vek"] = ch["vek"].fillna(-1).astype(int)
            ch["kraj_nuts_kod"] = ch["kraj_nuts_kod"].fillna("CZ999")
            parts.append(ch.groupby(["rok", "mesic", "vek", "kraj_nuts_kod"])
                           .size().rename("pripady").reset_index())
        agg = (pd.concat(parts)
                 .groupby(["rok", "mesic", "vek", "kraj_nuts_kod"])["pripady"]
                 .sum().reset_index())
    finally:
        os.unlink(tmp_path)

    if agg.empty or agg.pripady.sum() < 1_000_000:
        raise ValueError("[covid_osoby] agregace podezřele malá — zdroj asi změnil formát")

    out_path = output_dir / "covid_osoby_agg.csv"
    agg.to_csv(out_path, index=False, encoding="utf-8")
    print(f"  [covid_osoby] {agg.pripady.sum():,} případů → {len(agg):,} agregovaných řádků → {out_path}")
    return str(out_path)


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

        if df.empty:
            raise ValueError(f"[{name}] stažený CSV je prázdný — zdroj pravděpodobně změnil formát")

        # Sjednotime nazev datumoveho sloupce
        if "datum" in df.columns:
            df["datum"] = pd.to_datetime(df["datum"]).dt.date

        out_path = output_dir / f"{name}.csv"
        df.to_csv(out_path, index=False, encoding="utf-8")
        print(f"  [{name}] {len(df):,} radku → {out_path}")
        downloaded.append(str(out_path))

    downloaded.append(_download_osoby_aggregate(output_dir))
    return downloaded


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    download(root / "data" / "mzcr")

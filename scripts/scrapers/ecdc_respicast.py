"""
ECDC RespiCast — týdenní pravděpodobnostní předpovědi ILI a ARI, řádky za ČR.
Zdroj: github.com/european-modelling-hubs/RespiCast-SyndromicIndicators
       (hub provozují ECDC, ISI Foundation a LSHTM; web respicast.ecdc.europa.eu)
Licence: repozitář ŽÁDNOU licenci neuvádí — před zobrazením na portálu se zeptat
         hubu (European.Modelling.Hub@ecdc.europa.eu) na podmínky a znění atribuce
Aktualizace: týdně (čtvrtek), soubor za každé kolo předpovědí od 10/2024

Bereme ensemble hubu (`respicast-hubEnsemble`), ne jednotlivé modely — ensemble
bývá spolehlivější než kterýkoli jeho člen. K němu referenční model hubu
(`respicast-quantileBaseline`, „bude to jako minulý týden“ s nejistotou
z minulých změn): bez něj nejde říct, jestli předpověď vůbec něco přidává. Předpovídá se stejná řada, nad kterou počítáme MEM (ILI a ARI
na 100 tis. z ERVISS), takže jde předpověď rovnou porovnat s našimi prahy.

Horizont 1 není budoucnost, ale právě uplynulý týden, za který ještě nejsou
konsolidovaná data; skutečný výhled jsou horizonty 2–4.

Soubor jednoho kola se po zveřejnění nemění → cache podle jména (stejně jako
krajská PDF v szu_weekly). Držíme celou historii kol, ne jen poslední: bez ní
nejde zpětně změřit, jak se předpovědi pro ČR trefovaly.

Výstup:
  respicast_cz.csv — model (ensemble/baseline), kolo (origin_date), ukazatel
                     (ILI/ARI), tyden_do (target_end_date, neděle), horizont,
                     kvantil, hodnota
"""

import io
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

REPO = "european-modelling-hubs/RespiCast-SyndromicIndicators"
MODELS = {"ensemble": "respicast-hubEnsemble", "baseline": "respicast-quantileBaseline"}
LISTING_URL = f"https://api.github.com/repos/{REPO}/contents/model-output"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/model-output"
LOCATION = "CZ"
TARGETS = {"ILI incidence": "ILI", "ARI incidence": "ARI"}
MAX_AGE_DAYS = 45


def _list_rounds(model: str) -> list[str]:
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):      # v CI; bez tokenu platí limit 60 dotazů/h na IP
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    resp = requests.get(f"{LISTING_URL}/{model}", headers=headers, timeout=60)
    resp.raise_for_status()
    names = sorted(f["name"] for f in resp.json() if f["name"].endswith(f"-{model}.csv"))
    if not names:
        raise ValueError(f"{LISTING_URL}/{model}: žádný soubor *-{model}.csv — hub změnil strukturu")
    return names


def _parse(content: bytes, name: str) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(content))
    required = {"origin_date", "target", "target_end_date", "horizon", "location",
                "output_type", "output_type_id", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name}: chybí očekávané sloupce {sorted(missing)} — hub změnil formát")

    # Medián je v souboru dvakrát (output_type "median" i kvantil 0.5) — stačí kvantily.
    df = df[(df["location"] == LOCATION) & (df["output_type"] == "quantile")
            & df["target"].isin(TARGETS)]
    return pd.DataFrame({
        "kolo": df["origin_date"], "ukazatel": df["target"].map(TARGETS),
        "tyden_do": df["target_end_date"], "horizont": df["horizon"].astype(int),
        "kvantil": df["output_type_id"].astype(float), "hodnota": df["value"].astype(float),
    })


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = output_dir / "respicast_cache"
    cache.mkdir(exist_ok=True)

    fetched = 0
    frames = []
    for label, model in MODELS.items():
        names = _list_rounds(model)
        print(f"  [ecdc_respicast] {label}: {len(names)} kol ({names[0][:10]} – {names[-1][:10]})")

        newest = date.fromisoformat(names[-1][:10])
        if label == "ensemble" and newest < date.today() - timedelta(days=MAX_AGE_DAYS):
            raise ValueError(f"poslední kolo předpovědí je {newest} — hub přestal přibývat")

        for name in names:
            cached = cache / name
            if not cached.exists():
                resp = requests.get(f"{RAW_URL}/{model}/{name}", timeout=120)
                resp.raise_for_status()
                cached.write_bytes(resp.content)
                fetched += 1
            frame = _parse(cached.read_bytes(), name)
            frame.insert(0, "model", label)
            frames.append(frame)

    out = pd.concat(frames).sort_values(["model", "kolo", "ukazatel", "horizont", "kvantil"])
    ensemble = out[out["model"] == "ensemble"]
    if ensemble[ensemble["kolo"] == ensemble["kolo"].max()].empty:
        raise ValueError(f"poslední kolo ensemble nemá žádnou předpověď pro {LOCATION}")

    out_path = output_dir / "respicast_cz.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(f"  [ecdc_respicast] staženo {fetched} nových kol, {len(out):,} radku "
          f"({out['kolo'].nunique()} kol s předpovědí pro {LOCATION}) → {out_path}")
    return [str(out_path)]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "ecdc")

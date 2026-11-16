"""
ENA (European Nucleotide Archive) — co je z českých vzorků veřejně v archivu.
Zdroj: www.ebi.ac.uk/ena/portal/api, datový portál `pathogen`
Licence: INSDC — sekvenční záznamy nenesou omezení užití ("no use restrictions or
         licensing requirements will be included in any sequence data records");
         EMBL-EBI neklade další omezení nad rámec původců dat
Aktualizace: průběžně (archiv přijímá denně)

Portál dosud ukazoval jen počty hlášených případů. Tohle je druhá strana téhož:
kolik genomických dat o českých patogenech je otevřeně dostupných. Není to
nemocnost — záznam vznikne tím, že někdo vzorek sekvenoval a data zveřejnil.

Dotaz: země odběru `country="Czech Republic"` (pozor, "Czechia" vrací nulu — ENA
drží starý název) a datový portál `pathogen`, tedy záznamy označené jako patogen.
Zemi vyplňuje ten, kdo data vkládá, takže záznam bez země se nezapočítá a všechna
čísla jsou spodní odhad.

Výstup:
  ena/ena_cz_totals.json   souhrnné počty (vzorky, běhy, sekvence) + počty studií
  ena/ena_cz_taxa.csv      na patogen ze seznamu: počet běhů a sekvencí, datum
                           posledního přírůstku (podle curated/genomics_taxa.yaml)
  ena/ena_cz_runs.csv      metadata sekvenačních běhů (jeden řádek = běh)
  ena/ena_cz_divisions.csv rozpad sekvencí podle taxonomické divize (viry,
                           bakterie, houby…) — ukazuje, jak široká značka
                           `pathogen` je
"""

import io
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

API = "https://www.ebi.ac.uk/ena/portal/api"
COUNTRY = "Czech Republic"
PORTAL = "pathogen"
# Jméno klienta bez osobních údajů; ENA kontakt nevyžaduje, jen slušné označení.
UA = "pathogen-portal-cz/1.0 (+https://pathogens.vm.cesnet.cz/)"
TIMEOUT = 180
PAUSE = 0.4            # mezi dotazy; ENA povoluje 50 dotazů/s, tohle je hluboko pod
RETRIES = 3

TAXA_FILE = Path(__file__).resolve().parents[2] / "curated" / "genomics_taxa.yaml"

# Pole metadat, která se u běhů berou. `base_count` a `read_count` jsou vyplněné
# vždy; ostatní jsou volitelná a jejich vyplněnost je sama o sobě zjištění.
RUN_FIELDS = [
    "run_accession", "sample_accession", "study_accession", "scientific_name", "tax_id",
    "collection_date", "first_public", "instrument_platform", "instrument_model",
    "library_strategy", "library_layout", "base_count", "read_count",
    "host", "isolation_source", "lat", "location", "strain", "isolate",
    "host_sex", "host_status",
]

# Taxonomické divize INSDC → skupina pro čtenáře. Dvoupísmenné kódy jsou
# INSDC konvence, ne naše zkratky.
DIVISIONS = {
    "VRL": {"cs": "viry", "en": "viruses"},
    "PRO": {"cs": "bakterie", "en": "bacteria"},
    "FUN": {"cs": "houby", "en": "fungi"},
    "INV": {"cs": "prvoci a bezobratlí", "en": "protozoa and invertebrates"},
    "PHG": {"cs": "bakteriofágy", "en": "bacteriophages"},
    "PLN": {"cs": "rostliny", "en": "plants"},
    "ENV": {"cs": "vzorky prostředí", "en": "environmental samples"},
    "MAM": {"cs": "savci", "en": "mammals"},
    "VRT": {"cs": "obratlovci", "en": "vertebrates"},
}


def _get(path: str, params: dict) -> str:
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(f"{API}/{path}", params=params,
                             headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"ENA {path} nedostupné: {last}")


def _count(result: str, query: str) -> int:
    # Odpověď je TSV s hlavičkou: řádek "count", pod ním číslo.
    lines = [ln.strip() for ln in _get("count", {"result": result, "dataPortal": PORTAL,
                                                 "query": query}).splitlines() if ln.strip()]
    if not lines or not lines[-1].isdigit():
        raise ValueError(f"ENA count vrátil {lines[:2]} — změnil se formát odpovědi?")
    return int(lines[-1])


def _search(result: str, query: str, fields: list[str]) -> pd.DataFrame:
    txt = _get("search", {"result": result, "dataPortal": PORTAL, "query": query,
                          "fields": ",".join(fields), "format": "tsv", "limit": 0})
    if not txt.strip():
        return pd.DataFrame(columns=fields)
    df = pd.read_csv(io.StringIO(txt), sep="\t", dtype=str, keep_default_na=False)
    missing = set(fields) - set(df.columns)
    if missing:
        raise ValueError(f"ENA search ({result}): chybí pole {sorted(missing)} — změnilo se API")
    return df


def _taxa() -> list[dict]:
    return yaml.safe_load(TAXA_FILE.read_text(encoding="utf-8"))


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = []
    cz = f'country="{COUNTRY}"'

    # ── 1. Souhrnné počty ────────────────────────────────────────────────────
    totals = {"country_query": COUNTRY, "data_portal": PORTAL,
              "fetched_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    for key, result in (("samples", "sample"), ("runs", "read_run"), ("sequences", "sequence")):
        totals[key] = _count(result, cz)
        time.sleep(PAUSE)
    print(f"  [ena_czech] vzorky {totals['samples']:,}, běhy {totals['runs']:,}, "
          f"sekvence {totals['sequences']:,}")

    # ── 2. Metadata běhů ─────────────────────────────────────────────────────
    runs = _search("read_run", cz, RUN_FIELDS)
    if len(runs) != totals["runs"]:
        # Nemusí jít o chybu (archiv se mění mezi dotazy), ale rozdíl má být vidět.
        print(f"  [ena_czech] ⚠ počet běhů {totals['runs']} ≠ stažených řádků {len(runs)}")
    totals["run_studies"] = int(runs["study_accession"].nunique()) if len(runs) else 0
    runs_path = output_dir / "ena_cz_runs.csv"
    runs.to_csv(runs_path, index=False, encoding="utf-8")
    out.append(str(runs_path))
    print(f"  [ena_czech] metadata běhů {len(runs):,} řádků → {runs_path}")
    time.sleep(PAUSE)

    # ── 3. Rozpad sekvencí podle divize ──────────────────────────────────────
    seqs = _search("sequence", cz, ["accession", "tax_division", "first_public", "study_accession"])
    totals["sequence_studies"] = int(seqs["study_accession"].nunique()) if len(seqs) else 0
    div = (seqs.groupby("tax_division").size().rename("sekvenci").reset_index()
           .sort_values("sekvenci", ascending=False))
    div["skupina_cs"] = [DIVISIONS.get(d, {}).get("cs", d) for d in div["tax_division"]]
    div["skupina_en"] = [DIVISIONS.get(d, {}).get("en", d) for d in div["tax_division"]]
    unknown = set(div["tax_division"]) - set(DIVISIONS)
    if unknown:
        # Nová divize se nesmí ztratit: projde do výstupu pod svým kódem a vypíše se.
        print(f"  [ena_czech] ⚠ neznámé taxonomické divize {sorted(unknown)} — beze změny názvu")
    div_path = output_dir / "ena_cz_divisions.csv"
    div.to_csv(div_path, index=False, encoding="utf-8")
    out.append(str(div_path))
    time.sleep(PAUSE)

    totals_path = output_dir / "ena_cz_totals.json"
    totals_path.write_text(json.dumps(totals, ensure_ascii=False, indent=1), encoding="utf-8")
    out.append(str(totals_path))

    # ── 4. Na patogen ze seznamu ─────────────────────────────────────────────
    # Dva dotazy na patogen (běhy + sekvence). Počet se dá zjistit i z /count,
    # ale datum posledního přírůstku ne, takže se rovnou tahá seznam dat
    # zveřejnění a obojí se spočítá z něj.
    rows = []
    for t in _taxa():
        rec = {"id": t["id"], "taxon": t["taxon"]}
        for key, result in (("behy", "read_run"), ("sekvence", "sequence")):
            df = _search(result, f'{cz} AND tax_tree({t["taxon"]})', ["first_public"])
            dates = sorted(d for d in df.get("first_public", []) if d)
            rec[key] = len(df)
            rec[f"{key}_posledni"] = dates[-1] if dates else ""
            rec[f"{key}_prvni"] = dates[0] if dates else ""
            time.sleep(PAUSE)
        rows.append(rec)
        print(f"  [ena_czech] {t['id']}: běhy {rec['behy']}, sekvence {rec['sekvence']}")

    taxa_path = output_dir / "ena_cz_taxa.csv"
    pd.DataFrame(rows).to_csv(taxa_path, index=False, encoding="utf-8")
    out.append(str(taxa_path))
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "ena")

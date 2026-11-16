"""
Genomická data z Česka → genomics_overview.json, genomics_lineages.json, genomics_amr.json.

Portál dosud ukazoval, kolik případů se nahlásilo. Tohle je druhá strana: co je
z českých vzorků veřejně uloženo v genomických archivech, co z toho plyne a které
linie u nás kolují. Nic se nesekvenuje ani nepočítá znovu — všechno jsou souhrny
nad hotovými veřejnými daty:

  genomics_overview  kolik dat je v archivu ENA, jak přibývají a čím se sekvenuje,
                     u kterých patogenů data jsou a jak dobře jsou popsaná
                     (scrapers/ena_czech.py)
  genomics_lineages  kolik českých kmenů je ve veřejných stromech příbuznosti,
                     do kterých linií patří a jak staré stromy jsou
                     (scrapers/nextstrain_czech.py)
  genomics_amr       geny rezistence, které v českých bakteriálních izolátech našel
                     nástroj AMRFinderPlus v NCBI (scrapers/ncbi_pathogen.py)

Co tyhle stránky NESMÍ tvrdit — hlídají to testy:
  - počet záznamů není nemocnost; vzniká tím, že někdo vzorek sekvenoval a data
    zveřejnil, a zemi u záznamu vyplňuje vkladatel, takže čísla jsou spodní odhad;
  - z několika desítek kmenů nelze usuzovat na zastoupení linií v populaci;
  - sekvenované izoláty nejsou náhodný vzorek (sekvenují se přednostně kmeny
    vybrané kvůli rezistenci, část je z odpadních vod), takže se z genů rezistence
    NEPOČÍTAJÍ procenta;
  - u ptačí chřipky musí být řečeno, že žádný český vzorek není od člověka.

Fakta a komentář skládá `facts.py`, stejně jako u respiračních bloků: text jde
později nahradit výstupem jazykového modelu beze změny stránky.

Použití:
    python scripts/compute_genomics.py

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_meta
from facts import Facts, fmt_int, fmt_num, fmt_pct, plural_cs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

ENA_DIR = DATA_DIR / "ena"
TREE_DIR = DATA_DIR / "nextstrain"
NCBI_DIR = DATA_DIR / "ncbi"
TAXA_FILE = ROOT / "curated" / "genomics_taxa.yaml"
TREES_FILE = ROOT / "curated" / "genomics_trees.yaml"

NEXTSTRAIN_URL = "https://nextstrain.org"
ENA_BROWSER = "https://www.ebi.ac.uk/ena/browser/view/"

# Skupiny patogenů v tabulce; pořadí je pořadí na stránce.
GROUPS = [
    ("respiratory", "Respirační", "Respiratory"),
    ("enteric", "Střevní a z potravin", "Enteric and foodborne"),
    ("sti", "Pohlavně a krví přenosné", "Sexually and blood-borne"),
    ("vector", "Přenášené klíšťaty a zvířaty", "Tick- and animal-borne"),
    ("invasive", "Invazivní", "Invasive"),
    ("amr", "Bakterie sledované kvůli rezistenci", "Bacteria monitored for resistance"),
]

# Sekvenační technologie: kód v ENA → popisek. Pořadí = pořadí vrstev ve sloupci,
# `other` sbírá zbytek (paleta portálu má na „ostatní“ šedý slot).
PLATFORMS = [
    ("ILLUMINA", "Illumina", "Illumina"),
    ("OXFORD_NANOPORE", "Oxford Nanopore", "Oxford Nanopore"),
    ("other", "ostatní", "other"),
]

# Pole metadat, jejichž vyplněnost se ukazuje. Pořadí = pořadí v grafu.
QUALITY_FIELDS = [
    ("collection_date", "datum odběru", "collection date"),
    ("isolation_source", "zdroj izolace", "isolation source"),
    ("host", "hostitel", "host"),
    ("lat", "souřadnice místa", "coordinates of the site"),
    ("host_sex", "pohlaví hostitele", "host sex"),
]
# Hodnoty, které ENA používá místo prázdna. Záznam s nimi není vyplněný.
EMPTY = {"", "missing", "not provided", "not collected", "not applicable", "unknown",
         "na", "n/a", "none", "restricted access", "not available"}

# Skupiny genů rezistence. Výběr není úplný seznam rezistence — jsou to geny,
# kvůli kterým se v Evropě sleduje právě tenhle druh (invazivní kmeny, nemocniční
# nákazy). Klíč `organisms` říká, u kterých druhů má skupina smysl; u ostatních by
# nález byl buď nemožný, nebo bez výpovědi.
GENE_GROUPS = [
    {"id": "carbapenemase", "organisms": ["klebsiella", "e-coli"],
     "pattern": r"^bla(KPC|NDM|OXA-(23|40|48|58|181|232|244)|VIM|IMP)",
     "cs": "karbapenemázy", "en": "carbapenemases",
     "note_cs": "enzymy rozkládající karbapenemy, tedy antibiotika poslední volby",
     "note_en": "enzymes that break down carbapenems, the antibiotics of last resort"},
    {"id": "esbl", "organisms": ["klebsiella", "e-coli"], "pattern": r"^blaCTX-M",
     "cs": "širokospektré beta-laktamázy (CTX-M)", "en": "extended-spectrum beta-lactamases (CTX-M)",
     "note_cs": "enzymy rozkládající běžné penicilinové a cefalosporinové antibiotika",
     "note_en": "enzymes that break down common penicillins and cephalosporins"},
    {"id": "vancomycin", "organisms": ["e-faecium"], "pattern": r"^van[AB]$",
     "cs": "rezistence k vankomycinu (vanA, vanB)", "en": "vancomycin resistance (vanA, vanB)",
     "note_cs": "kmeny označované jako VRE", "note_en": "strains known as VRE"},
    {"id": "methicillin", "organisms": ["s-aureus"], "pattern": r"^mec[AC]$",
     "cs": "rezistence k methicilinu (mecA, mecC)", "en": "methicillin resistance (mecA, mecC)",
     "note_cs": "kmeny označované jako MRSA", "note_en": "strains known as MRSA"},
]

# Délka genomu (bází) pro odhad pokrytí, klíčem je vědecký název tak, jak ho píše
# ENA ve sloupci `scientific_name`. Řádový odhad, ne přesný výpočet — proto se
# zaokrouhluje na celé násobky a v textu stojí „typicky“.
GENOME_SIZE = {
    "Klebsiella pneumoniae": 5_500_000,
    "Mycobacterium tuberculosis": 4_400_000,
    "Escherichia coli": 5_000_000,
    "Staphylococcus aureus": 2_800_000,
    "Enterococcus faecium": 2_900_000,
    "Listeria monocytogenes": 2_950_000,
}
# Vzorky z odpadních vod a prostředí — podpírají větu, že sekvenované izoláty
# nejsou náhodný vzorek pacientů.
ENVIRONMENTAL = re.compile(r"wastewater|sewage|wwtp|effluent|influent|environment", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _taxa() -> list[dict]:
    return yaml.safe_load(TAXA_FILE.read_text(encoding="utf-8"))


def _trees() -> list[dict]:
    return yaml.safe_load(TREES_FILE.read_text(encoding="utf-8"))


def _mid_sentence(name: str) -> str:
    """„Ptačí chřipka H5N1“ → „ptačí chřipka H5N1“, ale „SARS-CoV-2“ zůstane: zkratka se nezmenšuje."""
    return name if len(name) < 2 or name[1].isupper() else name[0].lower() + name[1:]


def czech_date(iso: str, lang: str) -> str:
    """'2026-09-30' → '30. 9. 2026' / '30 September 2026'. Prázdný řetězec projde."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    if lang == "cs":
        return f"{d.day}. {d.month}. {d.year}"
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    return f"{d.day} {months[d.month - 1]} {d.year}"


def month_label(iso: str, lang: str) -> str:
    """'2026-09-30' → '9/2026'. Kratší tvar do tabulky, kde jde jen o stáří."""
    if not iso:
        return ""
    return f"{int(iso[5:7])}/{iso[:4]}"


def _filled(series: pd.Series) -> int:
    return int(sum(1 for v in series if str(v).strip().lower() not in EMPTY))


# ── 1. Přehled archivu ───────────────────────────────────────────────────────

def build_overview() -> dict | None:
    totals_file = ENA_DIR / "ena_cz_totals.json"
    runs_file = ENA_DIR / "ena_cz_runs.csv"
    taxa_file = ENA_DIR / "ena_cz_taxa.csv"
    if not totals_file.exists() or not runs_file.exists() or not taxa_file.exists():
        print("  [genomics_overview] data ENA chybí — přeskakuji")
        return None

    totals = json.loads(totals_file.read_text(encoding="utf-8"))
    runs = pd.read_csv(runs_file, dtype=str, keep_default_na=False)
    taxa_counts = pd.read_csv(taxa_file, dtype=str, keep_default_na=False).set_index("id")
    divisions = pd.read_csv(ENA_DIR / "ena_cz_divisions.csv", keep_default_na=False)

    # ── přírůstky po letech a technologiích
    runs["rok"] = runs["first_public"].str[:4]
    runs["platforma"] = [p if p in {k for k, _, _ in PLATFORMS} else "other"
                         for p in runs["instrument_platform"]]
    this_year = date.today().year
    by_year = (runs.groupby(["rok", "platforma"]).size().unstack(fill_value=0)
               .reindex(columns=[k for k, _, _ in PLATFORMS], fill_value=0).sort_index())
    # Prvních pár let má jednotky záznamů; v grafu by to byly neviditelné sloupce,
    # proto se sečtou do jednoho údaje pod grafem.
    recent = [y for y in by_year.index if y and int(y) >= this_year - 10]
    older = int(by_year.loc[[y for y in by_year.index if y and int(y) < this_year - 10]].sum().sum())
    growth = {
        "years": recent,
        "platforms": [{"id": k, "label": cs, "label_en": en} for k, cs, en in PLATFORMS],
        "rows": [{"year": y, **{k: int(by_year.loc[y, k]) for k, _, _ in PLATFORMS},
                  "total": int(by_year.loc[y].sum()), "partial": int(y) == this_year}
                 for y in recent],
        "older_total": older,
        "older_until": str(this_year - 11),
    }

    # ── tabulka patogenů
    groups, none_of_them = [], []
    for gid, cs, en in GROUPS:
        rows = []
        for t in _taxa():
            if t["group"] != gid:
                continue
            c = taxa_counts.loc[t["id"]] if t["id"] in taxa_counts.index else None
            runs_n = int(c["behy"]) if c is not None else 0
            seqs_n = int(c["sekvence"]) if c is not None else 0
            last = max(((c["behy_posledni"] if c is not None else ""),
                        (c["sekvence_posledni"] if c is not None else "")))
            if runs_n == 0 and seqs_n == 0:
                none_of_them.append({"id": t["id"], "name": t["name"]["cs"],
                                     "name_en": t["name"]["en"]})
                continue
            rows.append({
                "id": t["id"], "name": t["name"]["cs"], "name_en": t["name"]["en"],
                "latin": t["latin"], "italic": bool(t.get("italic")),
                "runs": runs_n, "sequences": seqs_n,
                "last": last, "last_label": month_label(last, "cs"),
                "last_label_en": month_label(last, "en"),
                "stale_since": int(last[:4]) if last and int(last[:4]) <= this_year - 3 else None,
                "page": t.get("page"), "note": t.get("note"),
                "ena_url": f"{ENA_BROWSER}Taxon:{t['taxon']}",
            })
        if rows:
            groups.append({"id": gid, "label": cs, "label_en": en,
                           "rows": sorted(rows, key=lambda r: -(r["runs"] + r["sequences"]))})

    # ── jak dobře jsou data popsaná
    n = len(runs)
    quality_fields = [{"id": f, "label": cs, "label_en": en,
                       "filled": _filled(runs[f]), "share": round(100 * _filled(runs[f]) / n, 1)}
                      for f, cs, en in QUALITY_FIELDS if f in runs.columns] if n else []
    # Zpoždění mezi odběrem a zveřejněním. Jen záznamy s datem odběru na den —
    # u „2019“ není z čeho počítat.
    lag = []
    for coll, pub in zip(runs["collection_date"], runs["first_public"]):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", coll or "") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub or ""):
            try:
                lag.append((date.fromisoformat(pub) - date.fromisoformat(coll)).days
                            )
            except ValueError:
                pass
    lag.sort()
    def q(p):
        return lag[min(len(lag) - 1, int(p * len(lag)))] if lag else None
    bases = sum(int(b) for b in runs["base_count"] if str(b).isdigit())
    precision = {"day": sum(1 for c in runs["collection_date"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c or "")),
                 "month": sum(1 for c in runs["collection_date"] if re.fullmatch(r"\d{4}-\d{2}", c or "")),
                 "year": sum(1 for c in runs["collection_date"] if re.fullmatch(r"\d{4}", c or ""))}
    depth = []
    for species, size in GENOME_SIZE.items():
        values = sorted(int(b) / size for b in runs.loc[runs["scientific_name"] == species,
                                                        "base_count"] if str(b).isdigit())
        if len(values) >= 20:                      # pod dvaceti běhy nemá medián smysl
            depth.append({"species": species, "median": round(values[len(values) // 2]),
                          "runs": len(values)})
    depth.sort(key=lambda d: -d["runs"])

    last_added = max([r for r in runs["first_public"] if r] + [""])
    quality = {"runs": n, "fields": quality_fields, "bases": bases,
               "lag_median": q(0.5), "lag_q1": q(0.25), "lag_q3": q(0.75), "lag_n": len(lag),
               "date_precision": precision, "depth": depth}

    # ── fakta a komentář
    f = Facts()
    f.count("n_seq", totals["sequences"])
    f.count("n_run", totals["runs"])
    f.count("n_sample", totals["samples"])
    f.text("d_last", czech_date(last_added, "cs"), czech_date(last_added, "en"))
    f.fact("totals", "measurement",
           "V archivu ENA je z českých vzorků {n_seq} sestavených sekvencí, {n_run} sekvenačních "
           "běhů a {n_sample} vzorků (záznamy označené jako patogen).",
           "The ENA archive holds {n_seq} assembled sequences, {n_run} sequencing runs and "
           "{n_sample} samples from Czech specimens (records tagged as pathogen).", required=True)
    f.say("V evropském archivu sekvencí ENA je z českých vzorků {n_seq} sestavených sekvencí "
          "a {n_run} sekvenačních běhů.",
          "The European Nucleotide Archive holds {n_seq} assembled sequences and {n_run} "
          "sequencing runs from Czech specimens.", ["totals"])
    f.fact("last_added", "meta",
           "Poslední záznam přibyl {d_last}.", "The latest record was added on {d_last}.")
    f.say("Poslední přibyl {d_last}.", "The latest was added on {d_last}.", ["last_added"],
          optional=True)
    f.fact("caveat_not_cases", "caveat",
           "Počet záznamů není nemocnost: záznam vznikne tím, že někdo vzorek sekvenoval a data "
           "zveřejnil.",
           "The number of records is not an illness count: a record exists because someone "
           "sequenced a specimen and published the data.", required=True)
    f.say("Počet záznamů není nemocnost; říká jen, kolik dat někdo zveřejnil.",
          "The number of records is not an illness count; it only says how much data someone "
          "has published.", ["caveat_not_cases"])
    if lag:
        f.num("lag_years", q(0.5) / 365.25)
        f.fact("lag", "measurement",
               "Od odběru po zveřejnění uplyne obvykle {lag_years} roku (medián).",
               "The time from collection to publication is usually {lag_years} years (median).")
        f.say("Od odběru po zveřejnění uplyne obvykle {lag_years} roku, archiv proto popisuje "
              "minulost, ne běžící situaci.",
              "Collection to publication usually takes {lag_years} years, so the archive "
              "describes the past, not the current situation.", ["lag"], optional=True)
    f.fact("caveat_country", "caveat",
           "Zemi odběru vyplňuje ten, kdo data vkládá; záznam bez země se nezapočítá, čísla jsou "
           "proto spodní odhad.",
           "The country of collection is filled in by whoever submits the data; a record without "
           "a country is not counted, so the figures are a lower bound.", required=True)
    f.say("Zemi u záznamu vyplňuje vkladatel, čísla jsou proto spodní odhad.",
          "The country is filled in by the submitter, so the figures are a lower bound.",
          ["caveat_country"])
    f.fact("source", "meta",
           "Zdroj: European Nucleotide Archive (EMBL-EBI); součty za Česko počítá Pathogen Portal.",
           "Source: European Nucleotide Archive (EMBL-EBI); the totals for Czechia are computed "
           "by Pathogen Portal.")

    return {"generated_at": _now(), "fetched_at": totals.get("fetched_at"),
            "totals": {k: totals[k] for k in
                       ("samples", "runs", "sequences", "run_studies", "sequence_studies")},
            "last_added": last_added, "growth": growth, "groups": groups, "none": none_of_them,
            "divisions": [{"code": r["tax_division"], "label": r["skupina_cs"],
                           "label_en": r["skupina_en"], "count": int(r["sekvenci"])}
                          for _, r in divisions.iterrows()],
            "quality": quality, **f.block()}


# ── 2. Linie ve veřejných stromech ───────────────────────────────────────────

def build_lineages() -> dict | None:
    main_file = TREE_DIR / "nextstrain_cz.csv"
    if not main_file.exists():
        print("  [genomics_lineages] data Nextstrainu chybí — přeskakuji")
        return None
    main = pd.read_csv(main_file, dtype=str, keep_default_na=False).set_index("id")
    clades = pd.read_csv(TREE_DIR / "nextstrain_cz_clades.csv", dtype=str, keep_default_na=False)

    trees = []
    for spec in _trees():
        if spec["id"] not in main.index:
            continue
        row = main.loc[spec["id"]]
        mine = clades[clades["id"] == spec["id"]]
        # Linie se sčítají přes hostitele; hostitel je vlastní rozpad (ptačí chřipka).
        by_clade = (mine[mine["linie"] != ""].groupby("linie")["pocet"]
                    .apply(lambda s: int(s.astype(int).sum())).sort_values(ascending=False))
        by_host = (mine[mine["hostitel"] != ""].groupby("hostitel")["pocet"]
                   .apply(lambda s: int(s.astype(int).sum())).sort_values(ascending=False))
        trees.append({
            "id": spec["id"], "name": spec["name"]["cs"], "name_en": spec["name"]["en"],
            # Tvar do věty: „strom pro ptačí chřipku H5N1“. Velká písmena uprostřed
            # věty vypadají jako překlep, zkratky v názvu ale velká zůstat musí.
            "name_lower": spec["name"].get("lower_cs", _mid_sentence(spec["name"]["cs"])),
            "group": spec["group"], "human": spec.get("human", True),
            "url": f"{NEXTSTRAIN_URL}/{spec['build'].replace('_', '/')}?f_country=Czech+Republic",
            "tips": int(row["listu_celkem"]), "czech": int(row["listu_cz"]),
            "from": row["odber_od"], "to": row["odber_do"],
            "from_label": czech_date(row["odber_od"], "cs"),
            "from_label_en": czech_date(row["odber_od"], "en"),
            "to_label": czech_date(row["odber_do"], "cs"),
            "to_label_en": czech_date(row["odber_do"], "en"),
            "updated": row["strom_prepocitan"],
            "updated_label": czech_date(row["strom_prepocitan"], "cs"),
            "updated_label_en": czech_date(row["strom_prepocitan"], "en"),
            "provenance": row["podklad"],
            "clades": [{"clade": k, "count": int(v)} for k, v in by_clade.items()],
            "hosts": [{"host": k, "count": int(v)} for k, v in by_host.items()],
        })
    if not trees:
        return None

    with_data = [t for t in trees if t["czech"]]
    empty = [t for t in trees if not t["czech"]]
    top = max(trees, key=lambda t: t["czech"])

    f = Facts()
    f.count("n_trees", len(trees))
    # Český tvar s malým začátečním písmenem (uprostřed věty), anglický beze změny —
    # lowercase by z „H5N1“ udělalo „h5n1“.
    f.text("top_name", top["name_lower"], top["name_en"])
    f.count("top_n", top["czech"])
    f.count("top_tips", top["tips"])
    f.fact("trees", "measurement",
           "Portál prochází {n_trees} veřejných stromů příbuznosti a hledá v nich kmeny s českou "
           "zemí odběru.",
           "The portal goes through {n_trees} public phylogenetic trees and looks for strains "
           "with Czechia as the country of collection.", required=True)
    f.fact("top", "measurement",
           "Nejvíc českých kmenů má strom {top_name}: {top_n} z {top_tips} listů.",
           "The tree with the most Czech strains is {top_name}: {top_n} of {top_tips} tips.",
           required=True)
    # Bez předložky před číslem: „ze 7“, ale „z 8“ — věta, která číslo neskloňuje, se nesplete.
    f.say("Portál prochází {n_trees} veřejných stromů příbuznosti; nejvíc českých kmenů má strom "
          "{top_name}: {top_n} z {top_tips} listů.",
          "The portal goes through {n_trees} public phylogenetic trees; the one with the most Czech "
          "strains is {top_name}: {top_n} of {top_tips} tips.", ["trees", "top"])
    if not top.get("human", True):
        f.fact("top_not_human", "caveat",
               "V tomto stromu nepochází žádný český vzorek od člověka.",
               "In this tree, no Czech specimen comes from a human.", required=True)
        f.say("Žádný český vzorek v něm není od člověka.",
              "No Czech specimen in it comes from a human.", ["top_not_human"])
    if empty:
        f.count("n_empty", len(empty))
        f.text("empty_names", ", ".join(t["name"] for t in empty),
               ", ".join(t["name_en"] for t in empty))
        f.fact("empty", "measurement",
               "Bez jediného českého kmene {n_empty} stromů: {empty_names}.",
               "Without a single Czech strain: {n_empty} trees — {empty_names}.")
        f.say("Bez jediného českého kmene zůstávají stromy: {empty_names}.",
              "The trees without a single Czech strain are: {empty_names}.", ["empty"],
              optional=True)
    f.fact("caveat_small", "caveat",
           "Z několika desítek kmenů nelze usuzovat na zastoupení linií v populaci; ukazují jen, "
           "co se z Česka do stromu dostalo.",
           "A few dozen strains cannot show how common lineages are in the population; they only "
           "show what reached the tree from Czechia.", required=True)
    f.say("Z několika desítek kmenů nelze usuzovat na zastoupení linií v populaci.",
          "A few dozen strains cannot show how common the lineages are in the population.",
          ["caveat_small"])
    f.fact("source", "meta",
           "Zdroj: veřejné stromy Nextstrain; počty českých kmenů počítá Pathogen Portal. "
           "Podkladová data stromů mají vlastní podmínky, proto se přebírají jen počty a odkaz.",
           "Source: public Nextstrain trees; the counts of Czech strains are computed by Pathogen "
           "Portal. The underlying data have their own terms, so only counts and a link are used.")

    fetch_file = TREE_DIR / "nextstrain_cz_fetch.json"
    fetched = (json.loads(fetch_file.read_text(encoding="utf-8")).get("fetched_at")
               if fetch_file.exists() else None)
    return {"generated_at": _now(), "fetched_at": fetched, "trees": trees,
            "with_data": [t["id"] for t in with_data], "empty": [t["id"] for t in empty],
            **f.block()}


# ── 3. Geny rezistence ───────────────────────────────────────────────────────

def _genes(value: str) -> set[str]:
    """'blaKPC-2=COMPLETE,fosA=PARTIAL' → {'blaKPC-2', 'fosA'}."""
    if not value:
        return set()
    return {part.split("=")[0].strip() for part in value.split(",") if part.strip()}


def build_amr() -> dict | None:
    path = NCBI_DIR / "ncbi_pd_cz.csv"
    if not path.exists():
        print("  [genomics_amr] data NCBI chybí — přeskakuji")
        return None
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    releases = {}
    rel_file = NCBI_DIR / "ncbi_pd_release.json"
    if rel_file.exists():
        releases = json.loads(rel_file.read_text(encoding="utf-8"))

    taxa = {t["id"]: t for t in _taxa()}
    organisms = []
    for key in df["id"].drop_duplicates():
        rows = df[df["id"] == key]
        t = taxa.get(key, {"name": {"cs": key, "en": key}, "latin": "", "italic": True})
        # Nadpis karty je organismus, ne nemoc: u listerií je `name` „Listerióza“.
        # NCBI počítá po skupinách (adresář „Klebsiella“ = celý rod), proto se bere
        # jeho vlastní označení, a teprve když chybí, vědecký název ze seznamu.
        organism = releases.get("releases", {}).get(key, {}).get("organism", "").replace("_", " ")
        with_genes = [r for _, r in rows.iterrows() if _genes(r["AMR_genotypes"])]
        years = sorted({r["collection_date"][:4] for _, r in rows.iterrows()
                        if re.match(r"\d{4}", r["collection_date"] or "")})
        found = []
        for grp in GENE_GROUPS:
            if key not in grp["organisms"]:
                continue
            rx = re.compile(grp["pattern"])
            hits = [r for r in with_genes if any(rx.search(g) for g in _genes(r["AMR_genotypes"]))]
            genes: dict[str, int] = {}
            for r in hits:
                for g in _genes(r["AMR_genotypes"]):
                    if rx.search(g):
                        genes[g] = genes.get(g, 0) + 1
            found.append({
                "id": grp["id"], "label": grp["cs"], "label_en": grp["en"],
                "note": grp["note_cs"], "note_en": grp["note_en"],
                "isolates": len(hits),
                "genes": [{"gene": g, "count": n}
                          for g, n in sorted(genes.items(), key=lambda kv: -kv[1])],
            })
        # Surové názvy zdroje jsou anglické a nejednotné („uti“, „mwwtps outlet“),
        # na českou stránku nepatří. Bere se z nich jen to, co podpírá výhradu:
        # kolik izolátů nepochází od pacienta, ale z odpadních vod či prostředí.
        environmental = sum(1 for v in rows["isolation_source"] if ENVIRONMENTAL.search(v or ""))
        organisms.append({
            "id": key, "name": t["name"]["cs"], "name_en": t["name"]["en"],
            "organism": organism or t.get("latin", ""),
            "latin": t.get("latin", ""), "italic": bool(t.get("italic", True)),
            "isolates": len(rows),
            "with_genotype": len(with_genes),
            "from_year": years[0] if years else None, "to_year": years[-1] if years else None,
            "groups": found,
            "environmental": environmental,
            "release": releases.get("releases", {}).get(key, {}).get("release"),
        })
    organisms.sort(key=lambda o: -o["isolates"])
    if not organisms:
        return None

    lead = organisms[0]
    lead_group = next((g for g in lead["groups"] if g["genes"]), None)

    f = Facts()
    f.text("org", lead["organism"], lead["organism"])
    f.count("n_iso", lead["isolates"])
    f.fact("isolates", "measurement",
           "{org}: z českých vzorků je v databázi NCBI {n_iso} sekvenovaných izolátů.",
           "{org}: the NCBI database holds {n_iso} sequenced isolates from Czech specimens.",
           required=True)
    f.say("{org}: z Česka je v databázi NCBI {n_iso} sekvenovaných izolátů.",
          "{org}: NCBI holds {n_iso} sequenced isolates from Czechia.", ["isolates"])
    if lead_group:
        f.text("grp", lead_group["label"], lead_group["label_en"])
        f.count("n_hit", lead_group["isolates"])
        f.text("gene", lead_group["genes"][0]["gene"], lead_group["genes"][0]["gene"])
        f.count("n_gene", lead_group["genes"][0]["count"])
        f.fact("group_hit", "measurement",
               "Aspoň jeden gen ze skupiny {grp} má {n_hit} z nich; nejčastější je {gene} "
               "({n_gene} izolátů).",
               "At least one gene from the group {grp} is present in {n_hit} of them; the most "
               "common is {gene} ({n_gene} isolates).", required=True)
        f.say("Aspoň jeden gen ze skupiny {grp} má {n_hit} z nich, nejčastěji {gene}.",
              "At least one gene from the group {grp} is present in {n_hit} of them, most often "
              "{gene}.", ["group_hit"])
    f.fact("caveat_not_share", "caveat",
           "Sekvenované izoláty nejsou náhodný vzorek: sekvenují se přednostně kmeny vybrané kvůli "
           "rezistenci a část vzorků pochází z odpadních vod. Počty proto neříkají, jak častá je "
           "rezistence u pacientů.",
           "Sequenced isolates are not a random sample: strains selected because of resistance are "
           "sequenced preferentially and some specimens come from wastewater. The counts therefore "
           "do not say how common resistance is among patients.", required=True)
    f.say("Sekvenují se přednostně kmeny vybrané kvůli rezistenci, počty proto neříkají, jak častá "
          "je rezistence u pacientů.",
          "Strains selected because of resistance are sequenced preferentially, so the counts do "
          "not say how common resistance is among patients.", ["caveat_not_share"])
    f.fact("source", "meta",
           "Zdroj: NCBI Pathogen Detection, výsledky nástroje AMRFinderPlus; výběr českých izolátů "
           "a součty dělá Pathogen Portal.",
           "Source: NCBI Pathogen Detection, AMRFinderPlus results; the selection of Czech isolates "
           "and the totals are done by Pathogen Portal.")

    return {"generated_at": _now(), "fetched_at": releases.get("fetched_at"),
            "organisms": organisms, "gene_groups": [g["id"] for g in GENE_GROUPS], **f.block()}


# ── Zápis ────────────────────────────────────────────────────────────────────

def is_stale(path: Path, obj: dict) -> bool:
    """
    True, když už hotový JSON vznikl ze stejného stažení jako `obj`.

    Genomické scrapery smějí selhat, aniž zastaví obnovu ostatních dat
    (run_all.py). Na disku pak zůstanou CSV z minula a přepsání JSON by posunulo
    `generated_at` — stránka by hlásila dnešní aktualizaci nad starými daty.
    """
    if not obj.get("fetched_at") or not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("fetched_at") == obj["fetched_at"]
    except (OSError, ValueError):
        return False


def save(name: str, obj: dict, force: bool = False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    if not force and is_stale(path, obj):
        print(f"  [{name}] vstup se od {obj['fetched_at']} nezměnil — nechávám, jak je "
              f"(--force přepíše)")
        return
    path.write_text(json.dumps(chart_meta.attach(name, obj), ensure_ascii=False), encoding="utf-8")
    print(f"  [{name}] → {path}")


def main() -> int:
    force = "--force" in sys.argv[1:]
    for name, build in (("genomics_overview", build_overview),
                        ("genomics_lineages", build_lineages),
                        ("genomics_amr", build_amr)):
        obj = build()
        if obj:
            save(name, obj, force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Registr patogenů — čtení a kontrola `curated/pathogens.yaml`, bez databáze a bez sítě.

Proč samostatný modul: registr čtou tři místa — loader do Postgresu
(`load_pathogens.py`), ověření proti autoritám (`verify_pathogens.py`) a testy.
Kdyby si každé z nich soubor vykládalo po svém, rozešly by se v tom, co je
platný záznam. Tady je jeden výklad a jedna kontrola; ostatní berou hotové řádky.

Co modul hlídá, je to, co by v databázi vypadalo věrohodně a bylo špatně:
kód MKN-10 připsaný jako „jen tento patogen“ dvěma patogenům, stránka, kterou
portál nemá, graf, který pipeline negeneruje, a rozchod s `genomics_taxa.yaml`,
kde je týž patogen zapsaný podruhé.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "curated" / "pathogens.yaml"
LABELS_EN = ROOT / "curated" / "labels_en.yaml"
READER_NAMES = ROOT / "curated" / "diagnosis_names.yaml"
GENOMICS_TAXA = ROOT / "curated" / "genomics_taxa.yaml"
CHARTS = ROOT / "charts.yaml"

CATEGORIES = {"virus", "bacteria", "fungus", "parasite", "other"}   # CHECK v db/init.sql
DASHBOARD_KINDS = {"surveillance", "genomics", "report", "external"}
LANGS = ("cs", "en")
# Schémata, která se plní z pole `taxon`, ne z `ids` — ENA přebírá čísla NCBI.
TAXON_SCHEMES = ("ncbi-taxon", "ena-taxon")

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ICD10_RE = re.compile(r"^[A-Z]\d{2}(\.\d)?$")
PATH_RE = re.compile(r"^/[a-z0-9/-]+/$")
ID_FORMATS = {"wikidata": re.compile(r"^Q\d+$"), "mesh": re.compile(r"^[DC]\d+$")}


class RegistryError(ValueError):
    """Registr neprošel kontrolou; zpráva nese všechny nálezy najednou."""


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read(path: Path = REGISTRY) -> dict:
    """Registr tak, jak je zapsaný. Kontrolu dělá `validate()`."""
    reg = _yaml(path) or {}
    for key in ("schemes", "dashboards", "diagnoses"):
        reg[key] = reg.get(key) or {}
    reg["pathogens"] = reg.get("pathogens") or []
    return reg


def vocabulary(reg: dict, labels_en: Path = LABELS_EN, reader: Path = READER_NAMES) -> dict:
    """
    Číselník diagnóz: kód → {name_cs, name_en, reader_cs, reader_en}.

    Oficiální názvy se berou z `labels_en.yaml` (kódy, které jsou v datech ISIN),
    čtenářské z `diagnosis_names.yaml`. Registr přidává jen kódy, které tam nejsou,
    a jen s anglickým názvem WHO — český úřední název, který v repozitáři není,
    zůstává prázdný.
    """
    voc: dict[str, dict] = {}

    def slot(code):
        return voc.setdefault(str(code), {"name_cs": None, "name_en": None,
                                          "reader_cs": None, "reader_en": None})

    for code, names in (_yaml(labels_en).get("diagnoses") or {}).items():
        slot(code).update(name_cs=names.get("cs"), name_en=names.get("en"))
    for code, names in (reg.get("diagnoses") or {}).items():
        s = slot(code)
        s["name_cs"] = s["name_cs"] or (names or {}).get("cs")
        s["name_en"] = s["name_en"] or (names or {}).get("en")
    for code, names in (_yaml(reader) or {}).items():
        slot(code).update(reader_cs=names.get("cs"), reader_en=names.get("en"))
    return voc


def known_charts(path: Path = CHARTS) -> set:
    return set(_yaml(path) or {})


def chart_is_known(chart: str, charts: set) -> bool:
    """Stejné pravidlo jako v chart_meta: záznam `isin_group_*` kryje celou skupinu."""
    return chart in charts or (chart.startswith("isin_group_") and "isin_group_*" in charts)


def _lang_lists(value, what, pid, errors) -> dict:
    """`synonyms` / `abbreviations`: {cs: [...], en: [...]}."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(LANGS):
        errors.append(f"{pid}: {what} má být {{cs: [...], en: [...]}}")
        return {}
    out = {}
    for lang, items in value.items():
        if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
            errors.append(f"{pid}: {what}.{lang} má být seznam neprázdných textů")
            continue
        out[lang] = items
    return out


def validate(reg: dict, *, voc: dict | None = None, charts: set | None = None,
             genomics_taxa: list | None = None, labels_codes: set | None = None,
             today: date | None = None) -> list:
    """
    Vrátí seznam nálezů (prázdný = registr je v pořádku).

    Nálezy se sbírají všechny, ne jen první: kdo opravuje registr, chce vidět
    celý rozsah, ne spouštět kontrolu desetkrát.
    """
    voc = vocabulary(reg) if voc is None else voc
    charts = known_charts() if charts is None else charts
    genomics_taxa = _yaml(GENOMICS_TAXA) if genomics_taxa is None else genomics_taxa
    labels_codes = (set(map(str, _yaml(LABELS_EN).get("diagnoses") or {}))
                    if labels_codes is None else labels_codes)
    today = today or date.today()
    errors: list[str] = []

    schemes, dashboards = reg["schemes"], reg["dashboards"]
    for scheme in TAXON_SCHEMES:
        if scheme not in schemes:
            errors.append(f"schemes: chybí {scheme} (plní se z pole taxon)")
    for scheme, s in schemes.items():
        for key in ("title", "authority", "uri"):
            if not (s or {}).get(key):
                errors.append(f"schemes.{scheme}: chybí {key}")
        if "{id}" not in ((s or {}).get("uri") or ""):
            errors.append(f"schemes.{scheme}: uri nemá zástupný znak {{id}}")

    for path, d in dashboards.items():
        d = d or {}
        if not PATH_RE.match(str(path)):
            errors.append(f"dashboards: {path!r} není cesta ve tvaru /…/")
        if d.get("kind") not in DASHBOARD_KINDS:
            errors.append(f"dashboards.{path}: kind {d.get('kind')!r} není z {sorted(DASHBOARD_KINDS)}")
        if d.get("chart") and not chart_is_known(d["chart"], charts):
            errors.append(f"dashboards.{path}: graf {d['chart']!r} není v charts.yaml")

    # Kód dvakrát (tady i v labels_en.yaml) = dvě pravdy o jednom názvu.
    for code in reg["diagnoses"]:
        if not ICD10_RE.match(str(code)):
            errors.append(f"diagnoses: {code!r} není kód MKN-10")
        if str(code) in labels_codes:
            errors.append(f"diagnoses: {code} už je v labels_en.yaml — tady ho smažte")

    ids, taxa, exact_owner, used_codes, used_dash = set(), {}, {}, set(), set()
    by_id = {p.get("id"): p for p in reg["pathogens"] if isinstance(p, dict)}
    for p in reg["pathogens"]:
        pid = p.get("id")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            errors.append(f"id {pid!r}: jen malá písmena, číslice a pomlčky")
            continue
        if pid in ids:
            errors.append(f"{pid}: id je v registru dvakrát")
        ids.add(pid)

        if p.get("category") not in CATEGORIES:
            errors.append(f"{pid}: category {p.get('category')!r} není z {sorted(CATEGORIES)}")

        taxon = p.get("taxon")
        if not isinstance(taxon, int) or isinstance(taxon, bool) or taxon <= 0:
            errors.append(f"{pid}: taxon má být kladné celé číslo (NCBI Taxonomy)")
        elif taxon in taxa:
            errors.append(f"{pid}: taxon {taxon} už má {taxa[taxon]}")
        else:
            taxa[taxon] = pid

        for key in ("scientific_name", "rank"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                errors.append(f"{pid}: chybí {key}")

        name = p.get("name") or {}
        for lang in LANGS:
            if not isinstance(name.get(lang), str) or not name[lang].strip():
                errors.append(f"{pid}: chybí name.{lang}")
        disease = p.get("disease")
        if disease is not None:
            for lang in LANGS:
                if not isinstance((disease or {}).get(lang), str) or not disease[lang].strip():
                    errors.append(f"{pid}: disease musí mít cs i en, nebo chybět celé")

        _lang_lists(p.get("synonyms"), "synonyms", pid, errors)
        _lang_lists(p.get("abbreviations"), "abbreviations", pid, errors)

        for scheme, value in (p.get("ids") or {}).items():
            if scheme in TAXON_SCHEMES:
                errors.append(f"{pid}: ids.{scheme} se nepíše, plní se z pole taxon")
            elif scheme not in schemes:
                errors.append(f"{pid}: ids.{scheme} — schéma není v oddílu schemes")
            elif scheme in ID_FORMATS and not ID_FORMATS[scheme].match(str(value)):
                errors.append(f"{pid}: ids.{scheme} {value!r} nemá tvar identifikátoru")

        exact, shared = p.get("icd10") or [], p.get("icd10_shared") or []
        for code in exact + shared:
            code = str(code)
            used_codes.add(code)
            if not ICD10_RE.match(code):
                errors.append(f"{pid}: {code!r} není kód MKN-10")
            elif code not in voc:
                errors.append(f"{pid}: kód {code} není v labels_en.yaml ani v oddílu diagnoses")
        for code in set(map(str, exact)) & set(map(str, shared)):
            errors.append(f"{pid}: kód {code} je v icd10 i v icd10_shared")
        for code in map(str, exact):
            # „Jen tento patogen“ nemůže platit pro dva; výjimkou je subtyp a jeho typ.
            other = exact_owner.get(code)
            if other and p.get("parent") != other and by_id.get(other, {}).get("parent") != pid:
                errors.append(f"{pid}: kód {code} má jako výlučný už {other} — patří do icd10_shared")
            exact_owner.setdefault(code, pid)

        pages = p.get("dashboards")
        if not isinstance(pages, list):
            errors.append(f"{pid}: dashboards má být seznam (i prázdný)")
            pages = []
        if len(set(pages)) != len(pages):
            errors.append(f"{pid}: stránka je v dashboards dvakrát")
        for page in pages:
            used_dash.add(page)
            if page not in dashboards:
                errors.append(f"{pid}: stránka {page!r} není v oddílu dashboards")

        parent = p.get("parent")
        if parent is not None and (parent == pid or parent not in by_id):
            errors.append(f"{pid}: parent {parent!r} není jiný záznam registru")

        verified = p.get("verified")
        if not isinstance(verified, date) or isinstance(verified, datetime):
            errors.append(f"{pid}: verified má být datum RRRR-MM-DD")
        elif verified > today:
            errors.append(f"{pid}: verified {verified} je v budoucnosti")

    for code in set(map(str, reg["diagnoses"])) - used_codes:
        errors.append(f"diagnoses: kód {code} žádný patogen nepoužívá")
    for path in set(dashboards) - used_dash:
        errors.append(f"dashboards: stránku {path} nemá žádný patogen")

    # Týž patogen je zapsaný i v genomics_taxa.yaml (čte ho compute_genomics.py).
    # Dokud jsou soubory dva, nesmí se rozejít v tom, co mají společné.
    for t in genomics_taxa or []:
        p = by_id.get(t["id"])
        if p is None:
            errors.append(f"{t['id']}: je v genomics_taxa.yaml, ale ne v registru")
            continue
        if p.get("taxon") != t["taxon"]:
            errors.append(f"{t['id']}: taxon {p.get('taxon')} ≠ {t['taxon']} v genomics_taxa.yaml")
        if t.get("page") and t["page"] not in (p.get("dashboards") or []):
            errors.append(f"{t['id']}: stránka {t['page']} z genomics_taxa.yaml chybí v dashboards")
    return errors


def load(path: Path = REGISTRY, **kwargs) -> dict:
    """Registr po kontrole; při nálezu vyhodí RegistryError se všemi nálezy."""
    reg = read(path)
    errors = validate(reg, **kwargs)
    if errors:
        raise RegistryError("registr patogenů neprošel kontrolou:\n  - " + "\n  - ".join(errors))
    return reg


# ── řádky pro databázi ───────────────────────────────────────────────────────

def identifiers(p: dict) -> list:
    """(schéma, hodnota). Taxon jde do obou taxonomických schémat — ENA čísluje stejně."""
    out = [(scheme, str(p["taxon"])) for scheme in TAXON_SCHEMES]
    out += [(scheme, str(value)) for scheme, value in sorted((p.get("ids") or {}).items())]
    return out


def terms(p: dict) -> list:
    """
    (jazyk, druh, termín, zdroj) — slovníček jednoho patogenu.

    Každý termín má zdroj: vědecké jméno je slovo taxonomie, čtenářský název
    slovo portálu. Bez toho by se z databáze nedalo poznat, co je norma a co
    redakční volba. Stejný termín téhož druhu a jazyka se zapíše jednou.
    """
    out = [("la", "scientific", p["scientific_name"], "ncbi-taxonomy")]
    if p.get("species"):
        out.append(("la", "species", p["species"], "ictv"))
    for lang in LANGS:
        out.append((lang, "pathogen", p["name"][lang], "portal"))
        if p.get("disease"):
            out.append((lang, "disease", p["disease"][lang], "portal"))
        for item in (p.get("synonyms") or {}).get(lang, []):
            out.append((lang, "synonym", item, "portal"))
        for item in (p.get("abbreviations") or {}).get(lang, []):
            out.append((lang, "abbreviation", item, "portal"))
    seen, unique = set(), []
    for row in out:
        if row[:3] not in seen:
            seen.add(row[:3])
            unique.append(row)
    return unique


def diagnoses(p: dict) -> list:
    """(kód, sdílený?)."""
    return ([(str(c), False) for c in p.get("icd10") or []]
            + [(str(c), True) for c in p.get("icd10_shared") or []])


def record_hash(p: dict) -> str:
    """
    Otisk záznamu. Loader podle něj pozná, že se záznam nezměnil, a nechá
    `updated_at` být. Počítá se z celého záznamu včetně odkazů na stránky a kódů,
    protože i jejich změna je změna patogenu v databázi.
    """
    canonical = json.dumps(p, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── čerstvost dat stránky ────────────────────────────────────────────────────

def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _as_timestamp(value):
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def chart_freshness(output_dir: Path, chart: str | None) -> tuple:
    """
    (data_through, data_generated_at) z vygenerovaného grafu, nebo (None, None).

    Dvě různá tvrzení, stejně jako na stránce portálu: kam data SAHAJÍ
    (`meta.period.end_date`; u souhrnů `posledni_datum`, u grafů bez časové osy
    konec rozmezí v `obdobi`) a kdy pipeline soubor ZAPSALA (`generated_at`).
    Chybějící soubor není chyba — databáze se dá naplnit i bez vygenerovaných
    grafů, jen bez těchto dvou sloupců.
    """
    if not chart:
        return None, None
    path = Path(output_dir) / f"{chart}.json"
    if not path.exists():
        return None, None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    meta = obj.get("meta") or {}
    # `obdobi` je "2021-01-01 – 2026-11-25" s půlčtverčíkem, jak ho píše generate_json.py.
    span_end = str(obj.get("obdobi") or "").split("–")[-1].strip()
    through = (_as_date((meta.get("period") or {}).get("end_date"))
               or _as_date(obj.get("posledni_datum")) or _as_date(span_end))
    generated = _as_timestamp(obj.get("generated_at")) or _as_timestamp(meta.get("generated_at"))
    return through, generated

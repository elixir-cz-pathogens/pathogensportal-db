"""
Metadata ke grafům — co se měří, v jaké jednotce, odkud to je a co z toho nejde vyčíst.

Chart JSON je jinak jen `labels` + `datasets`, tedy čísla s českými popisky.
Člověk si význam dočte z nadpisu stránky, stroj ne. Blok `meta` doplňuje metriku,
JEDNOTKU (počet a „na 100 000“ jsou dvě různá čísla), zrno období, zdroj, jeho
čerstvost a upozornění k datům. Bez toho nemá AI vrstva nad portálem šanci
odpovídat správně — a zlom v hlášení (EWS od 7/2025) vydá za epidemii.

Frontend blok ignoruje (Chart.js si bere jen `labels`/`datasets`), takže se
na webu nic nemění; čtou ho MCP, `/api/charts` a katalog zdrojů.

Skládá se ze tří vstupů:
  charts.yaml                   co který graf měří (ručně vedená mapa)
  curated/chart_titles.yaml     jak se graf jmenuje, česky a anglicky
  data/meta/source_metadata.json  co o zdrojích tvrdí jejich vydavatelé
                                  (vyrábí scripts/source_metadata.py)

Všechno je volitelné: bez nich se grafy vygenerují dál, jen bez metadat. Chybějící
popisek nesmí shodit pipeline, která veze vlastní data.
"""

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
CHARTS_META_FILE = ROOT / "charts.yaml"
CHART_TITLES_FILE = ROOT / "curated" / "chart_titles.yaml"
SOURCES_FILE = ROOT / "sources.yaml"
SOURCE_META_FILE = DATA_DIR / "meta" / "source_metadata.json"

# Zrna, u kterých je osa X čas a má smysl z ní číst rozsah období. U věkových
# skupin nebo krajů by „od 0-4 do 80+“ bylo matoucí, proto seznam, ne odhad.
TIME_GRAINS = {"day", "week", "month", "year", "season"}

# Číselníky, kterými jsou kódované řady grafu (`series_code` v charts.yaml).
# Kód je v `datasets[i].code`; tady je, co ten kód znamená.
CODE_SYSTEMS = {
    "icd10": {"id": "ICD-10", "name": "MKN-10 / ICD-10", "url": "https://icd.who.int/browse10/2019/en"},
}


def _load_charts() -> dict:
    try:
        return yaml.safe_load(CHARTS_META_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        print(f"  [meta] {CHARTS_META_FILE.name} chybí — grafy budou bez metadat")
        return {}


def _load_titles() -> dict:
    try:
        return yaml.safe_load(CHART_TITLES_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        print(f"  [meta] {CHART_TITLES_FILE.name} chybí — grafy budou bez názvu")
        return {}


def _load_open_sources() -> set:
    """
    Zdroje, jejichž podmínky dovolují volné další užití (`licence.open` v sources.yaml).

    Čte se registr v repozitáři, ne source_metadata.json: pod jakou licencí
    vydáváme vlastní výstup, je naše rozhodnutí zapsané v gitu, ne údaj, který se
    zjišťuje u vydavatele a při výpadku sítě chybí.
    """
    try:
        registry = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8")) or []
    except FileNotFoundError:
        return set()
    return {s["id"] for s in registry if (s.get("licence") or {}).get("open") is True}


def _load_sources() -> dict:
    if not SOURCE_META_FILE.exists():
        print(f"  [meta] {SOURCE_META_FILE.name} chybí — spusť scripts/source_metadata.py")
        return {}
    try:
        return json.loads(SOURCE_META_FILE.read_text(encoding="utf-8")).get("sources", {})
    except json.JSONDecodeError as e:
        print(f"  [meta] {SOURCE_META_FILE.name} je poškozený ({e}) — pokračuji bez něj")
        return {}


_CHARTS = _load_charts()
_TITLES = _load_titles()
_OPEN_SOURCES = _load_open_sources()
_SOURCES = _load_sources()


def spec_for(name: str) -> dict | None:
    """Záznam z charts.yaml; `isin_group_*` pokrývá celou skupinu grafů."""
    if name in _CHARTS:
        return _CHARTS[name]
    for key, spec in _CHARTS.items():
        if key.endswith("*") and name.startswith(key[:-1]):
            return spec
    return None


_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^(\d{4})$")
_KT_WEEK = re.compile(r"^KT (\d{1,2})/(\d{2})$")       # „KT 40/26“ = ISO týden 40 roku 2026


def _bounds(label: str, grain: str) -> tuple[date, date] | None:
    """
    První a poslední kalendářní den období, které popisek na ose označuje.

    Popisky jsou psané pro čtenáře grafu („KT 40/26“, „2018-01“, „2025“), stroj
    z nich datum nevyčte. Co nejde převést jistě, vrací None — sezóna
    „2012/2013“ nemá den, kterým začíná, zapsaný v datech, a datum, které by
    tu vzniklo odhadem, by vypadalo stejně důvěryhodně jako skutečné.
    """
    try:
        if m := _ISO_DAY.match(label):
            first = date(int(m[1]), int(m[2]), int(m[3]))
            # U týdenních řad je popiskem pondělí týdne (to_weekly, ISO týdny ERVISS).
            return first, (first + timedelta(days=6) if grain == "week" else first)
        if m := _ISO_MONTH.match(label):
            y, mth = int(m[1]), int(m[2])
            return date(y, mth, 1), date(y, mth, calendar.monthrange(y, mth)[1])
        if m := _YEAR.match(label):
            return date(int(m[1]), 1, 1), date(int(m[1]), 12, 31)
        if m := _KT_WEEK.match(label):
            monday = date.fromisocalendar(2000 + int(m[2]), int(m[1]), 1)
            return monday, monday + timedelta(days=6)
    except ValueError:          # 31. únor, 53. týden v roce, který ho nemá
        return None
    return None


def _period(obj: dict, grain: str) -> dict | None:
    labels = obj.get("labels")
    if not labels or not isinstance(labels, list):
        return None
    period = {"start": str(labels[0]), "end": str(labels[-1]), "points": len(labels)}
    first, last = _bounds(str(labels[0]), grain), _bounds(str(labels[-1]), grain)
    # Obě data, nebo žádné: polovina rozsahu by se četla jako „od … dodnes“.
    if first and last:
        period["start_date"] = first[0].isoformat()
        period["end_date"] = last[1].isoformat()
    return period


def licence_for(source_ids: list) -> dict:
    """
    Licence výstupu — pod čím smí graf někdo použít dál.

    Graf je cizí číslo plus naše zpracování. Zpracování licencovat můžeme, ale
    nemůžeme dát dál víc práv, než dal zdroj nám: CC BY 4.0 na grafu ze zdroje
    s licencí „nekomerčně, bez úprav“ by čtenáři slibovalo, co nám nikdo
    nedovolil. Proto CC BY 4.0 jen tam, kde volné užití dovolují VŠECHNY zdroje
    grafu. Jinak se žádná licence neuvádí (schválně ani `url` — stroj, který
    katalog sklízí, si dovětek nepřečte a viděl by jen otevřenou licenci)
    a `restricted_by` říká, čí podmínky rozhodují; jsou v `sources[].licence`.
    """
    restricted = [sid for sid in source_ids if sid not in _OPEN_SOURCES]
    if source_ids and not restricted:
        return {"id": "CC-BY-4.0",
                "name": {"cs": "CC BY 4.0", "en": "CC BY 4.0"},
                "url": "https://creativecommons.org/licenses/by/4.0/"}
    return {"id": "source-terms",
            "name": {"cs": "platí podmínky zdroje", "en": "the terms of the source apply"},
            "restricted_by": restricted}


def title_for(name: str) -> dict | None:
    """Název grafu `{"cs": …, "en": …}` z curated/chart_titles.yaml."""
    title = _TITLES.get(name)
    return dict(title) if isinstance(title, dict) and title.get("cs") and title.get("en") else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def meta_for(name: str, obj: dict | None = None, quiet: bool = False) -> dict | None:
    """
    Blok `meta` pro graf `name`. None, když graf není v charts.yaml.

    `obj` je hotový obsah grafu — použije se jen k odečtení rozsahu období
    z popisků; u grafů, které `labels` nemají (flu_mem, anomaly_signals),
    se vynechá.
    """
    spec = spec_for(name)
    if spec is None:
        if not quiet:
            print(f"  ⚠️  [{name}] není v charts.yaml — graf půjde ven bez metadat")
        return None

    source_ids = spec["source"] if isinstance(spec["source"], list) else [spec["source"]]
    meta = {"chart_id": name}
    title = title_for(name)
    if title:
        meta["title"] = title
    elif not quiet:
        print(f"  ⚠️  [{name}] nemá název v {CHART_TITLES_FILE.name}")
    meta |= {
        "metric": spec["metric"],
        "unit": spec["unit"],
        "grain": spec["grain"],
        "region": spec["region"],
        "generated_at": _now(),
        "source_ids": source_ids,
        "licence": licence_for(source_ids),
    }
    if obj and spec["grain"] in TIME_GRAINS:
        period = _period(obj, spec["grain"])
        if period:
            meta["period"] = period
    # Číselník se uvádí jen u grafu, kde nějaká řada kód opravdu nese — souhrnná
    # řada „Ostatní diagnózy“ žádný nemá a hlavička by slibovala, co v datech není.
    code_system = CODE_SYSTEMS.get(spec.get("series_code"))
    if code_system and any(isinstance(d, dict) and d.get("code") for d in (obj or {}).get("datasets") or []):
        meta["series_code_system"] = code_system

    sources, caveats = [], []
    for sid in source_ids:
        rec = _SOURCES.get(sid)
        if not rec:
            continue
        sources.append({k: v for k, v in {
            "id": sid,
            "title": rec.get("title"),
            "publisher": rec.get("publisher"),
            "landing_page": rec.get("landing_page"),
            "licence": rec.get("licence"),
            "citation": rec.get("citation"),
            "modified_at_publisher": rec.get("modified_at_publisher"),
            "periodicity": rec.get("periodicity"),
            "fetched_at": rec.get("fetched_at"),
            "snapshot_date": rec.get("snapshot_date"),
        }.items() if v})
        caveats.extend(rec.get("caveats", []))

    if sources:
        meta["sources"] = sources
    if caveats:
        # Stejný caveat může přijít od víc zdrojů — v grafu má být jednou.
        meta["caveats"] = list({c["id"]: c for c in caveats}.values())
    return meta


def attach(name: str, obj: dict, quiet: bool = False) -> dict:
    """Vrátí kopii `obj` s blokem `meta`, nebo `obj` beze změny, když metadata nejsou."""
    meta = meta_for(name, obj, quiet=quiet)
    return {**obj, "meta": meta} if meta else obj

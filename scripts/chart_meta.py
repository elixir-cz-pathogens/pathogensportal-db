"""
Metadata ke grafům — co se měří, v jaké jednotce, odkud to je a co z toho nejde vyčíst.

Chart JSON je jinak jen `labels` + `datasets`, tedy čísla s českými popisky.
Člověk si význam dočte z nadpisu stránky, stroj ne. Blok `meta` doplňuje metriku,
JEDNOTKU (počet a „na 100 000“ jsou dvě různá čísla), zrno období, zdroj, jeho
čerstvost a upozornění k datům. Bez toho nemá AI vrstva nad portálem šanci
odpovídat správně — a zlom v hlášení (EWS od 7/2025) vydá za epidemii.

Frontend blok ignoruje (Chart.js si bere jen `labels`/`datasets`), takže se
na webu nic nemění; čtou ho MCP, `/api/charts` a katalog zdrojů.

Skládá se ze dvou vstupů:
  charts.yaml                   co který graf měří (ručně vedená mapa)
  data/meta/source_metadata.json  co o zdrojích tvrdí jejich vydavatelé
                                  (vyrábí scripts/source_metadata.py)

Obojí je volitelné: bez nich se grafy vygenerují dál, jen bez metadat. Chybějící
popisek nesmí shodit pipeline, která veze vlastní data.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
CHARTS_META_FILE = ROOT / "charts.yaml"
SOURCE_META_FILE = DATA_DIR / "meta" / "source_metadata.json"

# Zrna, u kterých je osa X čas a má smysl z ní číst rozsah období. U věkových
# skupin nebo krajů by „od 0-4 do 80+“ bylo matoucí, proto seznam, ne odhad.
TIME_GRAINS = {"day", "week", "month", "year"}


def _load_charts() -> dict:
    try:
        return yaml.safe_load(CHARTS_META_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        print(f"  [meta] {CHARTS_META_FILE.name} chybí — grafy budou bez metadat")
        return {}


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
_SOURCES = _load_sources()


def spec_for(name: str) -> dict | None:
    """Záznam z charts.yaml; `isin_group_*` pokrývá celou skupinu grafů."""
    if name in _CHARTS:
        return _CHARTS[name]
    for key, spec in _CHARTS.items():
        if key.endswith("*") and name.startswith(key[:-1]):
            return spec
    return None


def _period(obj: dict) -> dict | None:
    labels = obj.get("labels")
    if not labels or not isinstance(labels, list):
        return None
    return {"start": str(labels[0]), "end": str(labels[-1]), "points": len(labels)}


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
    meta = {
        "chart_id": name,
        "metric": spec["metric"],
        "unit": spec["unit"],
        "grain": spec["grain"],
        "region": spec["region"],
        "generated_at": _now(),
        "source_ids": source_ids,
    }
    if obj and spec["grain"] in TIME_GRAINS:
        period = _period(obj)
        if period:
            meta["period"] = period

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

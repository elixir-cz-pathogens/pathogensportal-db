"""
Stahuje metadata O ZDROJÍCH — ne data, ale to, co o datech tvrdí jejich vydavatel.

Proč: u každého čísla na portálu má jít zjistit, odkud pochází, pod jakou
licencí, a hlavně jak je staré. Dosud se datum aktualizace psalo ručně do
katalogu na webu a zastarávalo — text „soubor z ledna 2026 končí prosincem
2025“ nikdo neobejde, dokud si toho někdo nevšimne. Vydavatelé přitom čerstvost
sami publikují, jen každý jinak, a nikdo se jich neptal.

Čte `sources.yaml` (co a kde zjistit), zapisuje `$DATA_DIR/meta/source_metadata.json`.

Čtyři cesty, jak se k metadatům dostat — každá se zkouší samostatně:

  csvw       `*.csv-metadata.json` vedle CSV, standard W3C CSVW. Nejbohatší:
             název, popis, vydavatel, licence, dc:modified, seznam sloupců.
             Mají ho všechny tři sady ÚZIS a je to jediné místo, kde zdroj sám
             říká licenci — proto se jeho `dc:license` bere přednostně před
             tím, co máme opsané v registru.
  nkod       Národní katalog otevřených dat (data.gov.cz), JSON-LD podle DCAT.
             Dává periodicitu vydávání a trvalý odkaz na datovou sadu.
  http_head  HEAD na soubor — Last-Modified, ETag, velikost. Funguje i tam,
             kde žádný katalog není (MZČR, ČSÚ, WHO).
  github     Datum posledního commitu v repozitáři (ERVISS, RespiCast).

⚠️ Selhání zjišťování NESMÍ shodit pipeline. Metadata jsou doprovodná informace;
když ÚZIS zrovna neodpovídá, data se stáhnou dál a u zdroje se zapíše `errors`.
Opak by znamenal, že si portál nestáhne data kvůli tomu, že si nestáhl popisek.

Použití:
    python scripts/source_metadata.py            # všechny zdroje
    python scripts/source_metadata.py uzis-isin  # jen vybrané
"""

import json
import os
import sys
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
SOURCES_FILE = ROOT / "sources.yaml"
METHODOLOGY_FILE = ROOT / "methodology_changes.yaml"
OUT_FILE = DATA_DIR / "meta" / "source_metadata.json"

TIMEOUT = 30
HEADERS = {"User-Agent": "pathogensportal-db/source_metadata (+https://pathogens.vm.cesnet.cz)"}

# Periodicita v NKOD je IRI z evropského číselníku. Překlad na krátký kód, ať
# nemusí každý konzument znát publications.europa.eu.
FREQUENCY_CODES = {
    "ANNUAL": "annual", "ANNUAL_2": "biannual", "ANNUAL_3": "triannual",
    "MONTHLY": "monthly", "WEEKLY": "weekly", "DAILY": "daily",
    "QUARTERLY": "quarterly", "CONT": "continuous", "IRREG": "irregular",
    "NEVER": "never", "UNKNOWN": "unknown",
}


def _iso(value) -> str | None:
    """Datum na ISO 8601 v UTC. Vrací None, když se nedá rozumně přečíst."""
    if not value:
        return None
    try:
        if isinstance(value, (date, datetime)):
            dt = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
        elif value.endswith("GMT") or "," in value:      # HTTP formát
            dt = parsedate_to_datetime(value)
        else:                                            # ISO, případně s Z
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Na sekundy: zlomky sekund u data vydání datové sady nenesou informaci
    # a jen komplikují porovnávání na straně konzumentů.
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _get(url: str, accept: str | None = None) -> requests.Response:
    headers = dict(HEADERS)
    if accept:
        headers["Accept"] = accept
    resp = requests.get(url, timeout=TIMEOUT, headers=headers)
    resp.raise_for_status()
    return resp


# ── Jednotlivé způsoby zjišťování ────────────────────────────────────────────

def from_csvw(url: str) -> dict:
    """W3C CSVW popis vedle CSV souboru."""
    doc = _get(url).json()
    publisher = doc.get("dc:publisher") or {}
    licence = doc.get("dc:license") or {}
    columns = [c.get("name") for c in doc.get("tableSchema", {}).get("columns", []) if c.get("name")]
    out = {
        "title": doc.get("dc:title"),
        "description": doc.get("dc:description"),
        "publisher_claimed": publisher.get("schema:name"),
        "modified_at_publisher": _iso((doc.get("dc:modified") or {}).get("@value")),
        "keywords": doc.get("dcat:keyword") or [],
        "columns": columns,
    }
    if isinstance(licence, dict) and licence.get("@id"):
        out["licence_claimed"] = licence["@id"]
    return {k: v for k, v in out.items() if v}


def from_nkod(iri: str) -> dict:
    """Záznam v Národním katalogu otevřených dat (DCAT v JSON-LD)."""
    doc = _get(iri, accept="application/ld+json").json()
    nodes = doc.get("@graph", [doc]) if isinstance(doc, dict) else doc
    out = {"catalog_iri": iri}
    for node in nodes:
        types = node.get("@type") or []
        if "http://www.w3.org/ns/dcat#Dataset" not in (types if isinstance(types, list) else [types]):
            continue
        freq = node.get("accrualPeriodicity")
        if isinstance(freq, str):
            out["periodicity"] = FREQUENCY_CODES.get(freq.rsplit("/", 1)[-1], freq.rsplit("/", 1)[-1])
        themes = node.get("theme")
        if themes:
            out["themes"] = themes if isinstance(themes, list) else [themes]
        break
    return out


def from_http_head(url: str) -> dict:
    """Hlavičky souboru. Poslední záchrana tam, kde katalog není."""
    resp = requests.head(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
    # Některé API na HEAD neodpovídají korektně (WHO xMart vrací 405) — zkus GET
    # a čti jen hlavičky; stream=True, ať se netahá celé tělo.
    if resp.status_code >= 400:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS, stream=True, allow_redirects=True)
        resp.close()
    resp.raise_for_status()
    size = resp.headers.get("Content-Length")
    out = {
        "modified_at_publisher": _iso(resp.headers.get("Last-Modified")),
        "etag": resp.headers.get("ETag"),
        "media_type": (resp.headers.get("Content-Type") or "").split(";")[0] or None,
        "byte_size": int(size) if size and size.isdigit() else None,
    }
    return {k: v for k, v in out.items() if v is not None}


def from_github(repo: str) -> dict:
    """Datum posledního commitu — u dat publikovaných přes git je to datum vydání."""
    doc = _get(f"https://api.github.com/repos/{repo}/commits?per_page=1").json()
    if not isinstance(doc, list) or not doc:
        raise ValueError(f"GitHub API nevrátilo commity pro {repo}")
    commit = doc[0]["commit"]["committer"]["date"]
    return {
        "modified_at_publisher": _iso(commit),
        "repository": f"https://github.com/{repo}",
    }


DISCOVERY = {
    "csvw": from_csvw,
    "nkod": from_nkod,
    "http_head": from_http_head,
    "github": from_github,
}


# ── Doplnění z vlastních záznamů ─────────────────────────────────────────────

def _snapshot_dates(files: list[str]) -> dict:
    """Kdy jsme zdroj naposledy sami stáhli — z manifestu snapshotů."""
    manifest_path = DATA_DIR / "raw" / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    days = [manifest[f]["snapshot"] for f in files if f in manifest and "snapshot" in manifest[f]]
    if not days:
        return {}
    return {"snapshot_date": max(days), "files_tracked": len(days)}


def _caveats(ids: list[str], registry: list[dict]) -> list[dict]:
    """
    Upozornění k datům z registru metodických změn.

    Nekopírují se sem texty — bere se `popis` a `dopad` z methodology_changes.yaml,
    aby existoval jeden zdroj pravdy. Právě tohle brání AI vrstvě vydávat zlom
    v hlášení (EWS od 7/2025) za epidemii.
    """
    by_id = {str(r.get("id")): r for r in registry}
    out = []
    for cid in ids:
        rec = by_id.get(cid)
        if not rec:
            print(f"    ⚠️  caveat '{cid}' není v methodology_changes.yaml")
            continue
        out.append({
            "id": cid,
            "since": str(rec.get("od")) if rec.get("od") else None,
            "action": rec.get("akce"),
            "description": " ".join((rec.get("popis") or "").split()),
            "impact": rec.get("dopad"),
        })
    return out


def collect(only: list[str] | None = None) -> dict:
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8")) or []
    methodology = yaml.safe_load(METHODOLOGY_FILE.read_text(encoding="utf-8")) or []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    out = {}
    for src in sources:
        sid = src["id"]
        if only and sid not in only:
            continue
        print(f"  [{sid}]")

        record = {
            "id": sid,
            "publisher": src["publisher"],
            "landing_page": src.get("landing"),
            "licence": src.get("licence", {}),
            "files": src.get("files", []),
            "fetched_at": fetched_at,
        }
        errors = {}

        for key, value in (src.get("discovery") or {}).items():
            if key == "manual":
                record["modified_at_publisher"] = _iso(value)
                record["modified_source"] = "manual"
                continue
            if key == "manual_note":
                record["manual_note"] = " ".join(str(value).split())
                continue
            fn = DISCOVERY.get(key)
            if not fn:
                errors[key] = "neznámý způsob zjišťování"
                continue
            try:
                found = fn(value)
            except Exception as e:
                # Zdroj může být dočasně mimo. Zapiš to a pokračuj — metadata
                # ostatních zdrojů ani samotná data tím trpět nemají.
                errors[key] = f"{type(e).__name__}: {e}"
                print(f"    ✗ {key}: {type(e).__name__}")
                continue
            # Dřívější, bohatší zjištění nepřepisovat chudším: pořadí v
            # sources.yaml je csvw → nkod → http_head, tedy od nejpřesnějšího.
            # Datum z CSVW proto přebije Last-Modified, který u velkých souborů
            # bývá datum posledního přegenerování, ne datum nových dat.
            if found.get("modified_at_publisher") and "modified_at_publisher" not in record:
                record["modified_source"] = key
            for k, v in found.items():
                record.setdefault(k, v)
            print(f"    ✓ {key}")

        # Licence od zdroje má přednost před tím, co máme opsané v registru.
        claimed = record.pop("licence_claimed", None)
        if claimed and claimed != (record.get("licence") or {}).get("url"):
            record.setdefault("licence", {})["url_claimed_by_source"] = claimed

        record.update(_snapshot_dates(src.get("files", [])))
        caveats = _caveats(src.get("methodology") or [], methodology)
        if caveats:
            record["caveats"] = caveats
        if errors:
            record["errors"] = errors

        out[sid] = record

    return {"generated_at": fetched_at, "sources": out}


def write(result: dict, path: Path = OUT_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    only = argv or None
    result = collect(only)
    path = write(result)

    ok = [s for s in result["sources"].values() if not s.get("errors")]
    failed = {sid: s["errors"] for sid, s in result["sources"].items() if s.get("errors")}
    stale = [s["id"] for s in result["sources"].values() if not s.get("modified_at_publisher")]

    print(f"\n  {len(ok)}/{len(result['sources'])} zdrojů bez chyby → {path}")
    if stale:
        print(f"  bez data aktualizace: {', '.join(stale)}")
    for sid, errs in failed.items():
        for key, msg in errs.items():
            print(f"  ! {sid}/{key}: {msg}")

    # Chyba u části zdrojů není důvod k nenulovému exit kódu — metadata jsou
    # doprovodná. Nulový výsledek ale znamená, že nefunguje nic, a to hlásit chceme.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

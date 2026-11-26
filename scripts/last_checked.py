"""
Kdy pipeline naposledy ověřila graf u zdroje → last_checked.json.

Proč: datum u panelu se čte z dat a posune se jen tehdy, když se data změní —
tak to má být. Jenže roční CSV ISIN se mění jednou ročně a pipeline se na něj
přitom ptá každou hodinu. Stránka Signály tak čtyři dny ukazovala „Aktualizace:
22. 11.“ a čtenář neměl jak poznat, jestli se zdroj nezměnil, nebo jestli
portál přestal běžet. Chybělo druhé tvrzení: „naposledy jsme se ptali tehdy
a nic novějšího tam nebylo“.

Ta dvě tvrzení se nesmějí slít. Datum dat (`generated_at`, poslední popisek
osy) zůstává, jak je; tenhle soubor nese jen datum ověření a portál ho ukazuje
vedle, ne místo něj.

Graf je ověřený, když platí obojí:
  1. scraper každého jeho zdroje (charts.yaml → `source`) se zdroje opravdu
     zeptal a doběhl bez chyby — zapisuje run_all.py přes `record_sources()`;
  2. generátor graf PO tom ověření dokončil: buď ho přepsal, nebo nad novým
     stažením zjistil, že se nezměnil — hlásí `mark_chart()` v každém save().
Druhá podmínka není formalita: generátory za `||` v Dockerfile smějí selhat,
aniž zastaví ostatní. Zdroj je pak ověřený, ale graf zůstal starý, a „ověřeno
dnes“ by u něj tvrdilo, co nikdo nezkontroloval.

Datum ověření grafu je datum nejstaršího z ověření jeho zdrojů: žádný zdroj
není čerstvější, než tvrdíme. Graf, který podmínky nesplní, si nechá datum,
které už má.

V souboru jsou schválně jen DNY (UTC), žádný čas. Obsah se tak změní nejvýš
jednou denně na graf, i když pipeline běží každou hodinu, a soubor se
nepřepisuje, dokud se nezmění. Do portálu tím přibude nejvýš pár řádků denně,
ne hodinový commit. Zároveň je to změna, kterou automat na serveru podle
historie commitů v portálu nezahazuje: soubor, kde se změnilo jen
`generated_at` / `fetched_at`, necommituje, kdežto změnu data commituje
(`snapshot_date` v bloku `meta`). Proto sem ŽÁDNÉ časové razítko nepřidávat.

Stav mezi kroky jednoho běhu (a mezi běhy) je v $DATA_DIR/meta/checks.json:
    {"sources": {"uzis-isin": "2026-11-26T07:10:02Z", …},   poslední ověření zdroje
     "charts":  {"anomaly_signals": "2026-11-26T07:14:31Z", …}}  graf dokončen

Použití:
    python scripts/last_checked.py        # poslední krok řetězu v Dockerfile

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))
STATE_FILE = DATA_DIR / "meta" / "checks.json"
OUT_NAME = "last_checked.json"

_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(_STAMP)


def _read(path: Path) -> dict:
    """JSON objekt ze souboru; chybějící nebo poškozený soubor je prázdný stav."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def load_state(path: Path | None = None) -> dict:
    """Stav z checks.json; co nemá tvar `{jméno: "čas"}`, se zahodí."""
    doc = _read(path or STATE_FILE)
    return {key: {name: stamp for name, stamp in doc[key].items() if isinstance(stamp, str)}
            if isinstance(doc.get(key), dict) else {}
            for key in ("sources", "charts")}


def _write_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


# ── Zápis stavu během běhu ───────────────────────────────────────────────────

def record_sources(checked, when: datetime | None = None, path: Path | None = None) -> None:
    """
    Zapíše, které zdroje se v tomto běhu podařilo ověřit.

    `when` je ZAČÁTEK běhu, ne konec: ověření proběhlo nejdřív tehdy, a u běhu
    přes půlnoc tak nevznikne datum o den novější než skutečný dotaz.
    Zdroj, který v `checked` není (scraper selhal, nebo vrátil soubory z cache),
    si nechá čas posledního podařeného ověření.
    """
    path = path or STATE_FILE
    state = load_state(path)
    stamp = _stamp(when)
    for sid in checked:
        state["sources"][sid] = stamp
    _write_state(state, path)


def mark_chart(name: str, when: datetime | None = None, path: Path | None = None) -> None:
    """
    Generátor graf `name` dokončil — přepsal ho, nebo zjistil, že se nezměnil.

    Volá se z každého save(). Selhání zápisu nesmí shodit generátor, který veze
    vlastní data: bez záznamu jen graf nedostane nové datum ověření.
    """
    try:
        path = path or STATE_FILE
        state = load_state(path)
        state["charts"][name] = _stamp(when)
        _write_state(state, path)
    except OSError as e:
        print(f"  [last_checked] {name}: stav se nepodařilo zapsat ({e}) — datum ověření se neposune")


# ── Výpočet výstupu ──────────────────────────────────────────────────────────

def _day(stamp: str) -> str:
    return stamp[:10]


def _entries(doc: dict, key: str) -> dict:
    """`{jméno: "YYYY-MM-DD"}` z dřívějšího výstupu; cokoli jiného se zahodí."""
    section = doc.get(key)
    out = {}
    for name, rec in (section.items() if isinstance(section, dict) else ()):
        day = rec.get("checked") if isinstance(rec, dict) else None
        if isinstance(day, str) and len(day) == 10:
            out[name] = day
    return out


def build(state: dict, previous: dict, sources_of, existing=None) -> dict:
    """
    Obsah last_checked.json.

    `state`       stav z checks.json
    `previous`    dosavadní last_checked.json — odtud si graf bere datum, když
                  ho tento běh neověřil
    `sources_of`  jméno grafu → seznam id zdrojů, nebo None (graf není v charts.yaml)
    `existing`    jména grafů, které ve výstupním adresáři opravdu jsou; záznam
                  o grafu, který zmizel, se nepřenáší dál. None = nefiltrovat.
    """
    sources, charts = _entries(previous, "sources"), _entries(previous, "charts")

    for sid, stamp in state["sources"].items():
        sources[sid] = max(sources.get(sid, ""), _day(stamp))

    for name, done in state["charts"].items():
        ids = sources_of(name)
        if not ids:
            continue
        stamps = [state["sources"].get(sid) for sid in ids]
        # Každý zdroj musí být někdy ověřený a graf dokončený až po posledním
        # ověření každého z nich — jinak graf stojí na starším stažení, než jaké
        # stav pamatuje, a jeho datum neznáme.
        if not all(stamps) or done < max(stamps):
            continue
        # Datum se nikdy nevrací: po ztrátě stavu (nový svazek /data) by jinak
        # stránka ukázala starší ověření, než jaké už ukazovala.
        charts[name] = max(charts.get(name, ""), _day(min(stamps)))

    if existing is not None:
        charts = {name: day for name, day in charts.items() if name in existing}
    return {
        "timezone": "UTC",
        "charts": {name: {"checked": day} for name, day in sorted(charts.items())},
        "sources": {sid: {"checked": day} for sid, day in sorted(sources.items())},
    }


def _dump(out: dict) -> str:
    """Jeden graf na řádek: v gitu portálu má být vidět, kterému se datum posunulo."""
    parts = []
    for key, value in out.items():
        if isinstance(value, dict):
            rows = ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
                              for k, v in value.items())
            parts.append(f' {json.dumps(key)}: {{\n{rows}\n }}' if rows else f' {json.dumps(key)}: {{}}')
        else:
            parts.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _chart_sources(name: str):
    import chart_meta
    spec = chart_meta.spec_for(name)
    if spec is None:
        return None
    return spec["source"] if isinstance(spec["source"], list) else [spec["source"]]


def main() -> int:
    path = OUTPUT_DIR / OUT_NAME
    previous = _read(path)
    existing = {p.stem for p in OUTPUT_DIR.glob("*.json")} if OUTPUT_DIR.is_dir() else set()
    out = build(load_state(), previous, _chart_sources, existing)

    if not out["charts"]:
        # Bez stavu (první běh mimo run_all.py, prázdný svazek) není co tvrdit;
        # prázdný soubor by portálu nic neřekl.
        print("  [last_checked] žádný graf není ověřený — soubor nezapisuji")
        return 0
    if out == previous:
        print(f"  [last_checked] beze změny — nechávám, jak je ({len(out['charts'])} grafů)")
        return 0
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(out), encoding="utf-8")
    newest = max(rec["checked"] for rec in out["charts"].values())
    behind = sorted(name for name, rec in out["charts"].items() if rec["checked"] < newest)
    print(f"  [last_checked] {len(out['charts'])} grafů, poslední ověření {newest} → {path}")
    if behind:
        print(f"  [last_checked] se starším datem zůstává: {', '.join(behind)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

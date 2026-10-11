"""
Anglické popisky pro vygenerované JSON (PPDB-77).

Pipeline píše české popisky a anglický web je dřív překládal v prohlížeči
konečným slovníkem (pp-charts.js) — názvy diagnóz, věkové skupiny ani kraje
v něm nebyly. Tenhle modul přidá vedle českých polí volitelná `*_en`:

    labels       → labels_en      (seznam, u map slovník NUTS3 → název)
    datasets[i].label → label_en
    x_title / unit / x_unit → x_title_en / unit_en / x_unit_en

Česká pole se nemění, takže starší frontend funguje dál. Pole `*_en` se
přidá jen tam, kde se anglická verze od české liší (data, roky, „RSV“…
překlad nepotřebují).

Překlady jsou v curated/labels_en.yaml, klíčované kódy (MKN-10, NUTS3), kde
je data mají; pevné texty a věkové skupiny jdou přes `terms` a PATTERNS.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

LABELS_FILE = Path(__file__).resolve().parent.parent / "curated" / "labels_en.yaml"

# Popisky, které se skládají z proměnných částí (týden, roky, věk).
PATTERNS = [
    (re.compile(r"^KT (\d+)/(\d+)$"), r"Wk \1/\2"),
    (re.compile(r"^(\d+)–(\d+) (?:let|roky|roků)$"), r"\1–\2 years"),
    (re.compile(r"^0 let$"), "Under 1 year"),
    (re.compile(r"^(\d+)\+ let$"), r"\1+ years"),
    (re.compile(r"^(\d+) let a méně$"), r"\1 years and under"),
    (re.compile(r"^(\d+) let a více$"), r"\1 years and over"),
    (re.compile(r"^Věková skupina \((.+)\)$"), r"Age group (\1)"),
    (re.compile(r"^Počet případů \((.+)\)$"), r"Number of cases (\1)"),
    (re.compile(r"^DRK (.+)$"), r"DRC \1"),
]

# Stejné v obou jazycích — bez nich by se hlásily jako chybějící překlad.
SAME = {
    "RSV", "SARS-CoV-2", "ECMO", "Influenza B", "Influenza A/H3N2", "Influenza A/H1N1pdm",
    "Rhinovirus", "Adenovirus", "Parainfluenza", "Metapneumovirus", "Mycoplasma pneumoniae",
}

# Text bez písmen (datum, rok, sezóna „2021/22“, věk „0–9“, „80+“) se nepřekládá.
_HAS_LETTER = re.compile(r"[^\W\d_]")


@lru_cache(maxsize=1)
def _load() -> dict:
    data = yaml.safe_load(LABELS_FILE.read_text(encoding="utf-8"))
    lookup = dict(data["terms"])
    for d in data["diagnoses"].values():
        lookup[d["cs"]] = d["en"]
    for r in data["regions"].values():
        for cs in r["cs"]:
            lookup[cs] = r["en"]
    data["lookup"] = lookup
    return data


def diagnosis(code: str) -> str | None:
    d = _load()["diagnoses"].get(code)
    return d["en"] if d else None


def region(code: str, full: bool = False) -> str | None:
    r = _load()["regions"].get(code)
    return (r["en_full"] if full else r["en"]) if r else None


def translate(text):
    """Anglická verze textu; None, když ji neznáme (a text překlad potřebuje)."""
    if not isinstance(text, str) or text in SAME or not _HAS_LETTER.search(text):
        return text
    hit = _load()["lookup"].get(text)
    if hit is not None:
        return hit
    for rx, repl in PATTERNS:
        if rx.match(text):
            return rx.sub(repl, text)
    return None


def localize(obj: dict) -> tuple[dict, list[str]]:
    """Přidá `*_en` pole do grafového JSON; vrací (obj, texty bez překladu)."""
    missing: list[str] = []

    def en(text):
        out = translate(text)
        if out is None:
            missing.append(text)
            return text
        return out

    labels = obj.get("labels")
    if isinstance(labels, dict):        # mapa: NUTS3 → krátký název kraje
        mapped = {}
        for code, name in labels.items():
            mapped[code] = region(code) or en(name)
        obj["labels_en"] = mapped
    elif isinstance(labels, list):
        mapped = [en(x) for x in labels]
        if mapped != labels:
            obj["labels_en"] = mapped

    for d in obj.get("datasets") or []:
        if isinstance(d, dict) and "label" in d:
            out = en(d["label"])
            if out != d["label"]:
                d["label_en"] = out

    for key in ("x_title", "unit", "x_unit"):
        if isinstance(obj.get(key), str):
            out = en(obj[key])
            if out != obj[key]:
                obj[f"{key}_en"] = out
    return obj, sorted(set(missing))


def annotate_signal(rec: dict) -> dict:
    """Signál detektoru: anglický název diagnózy a kraje podle kódů."""
    name = diagnosis(rec.get("diagnoza", ""))
    if name:
        rec["diagnoza_nazev_en"] = name
    kraj = region(rec.get("kraj_kod", ""), full=True)
    if kraj:
        rec["kraj_nazev_en"] = kraj
    return rec

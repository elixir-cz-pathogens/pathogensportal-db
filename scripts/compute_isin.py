"""
Infekční nemoci — přehledové bloky → isin_weekly.json, isin_overview.json.

Panel „Infekční nemoci — přehled“ stál jen na otevřeném CSV ISIN, které se
obnovuje jednou ročně: v listopadu ukazoval data do loňského prosince a roční
počty bez upozornění, že část nárůstu roku 2025 je změna hlášení. Tyhle dva
soubory to doplňují:

  isin_weekly    týdenní počty 11 diagnóz až do minulého týdne, zvlášť záznamy
                 z hlášení EWS; počet od začátku roku, poslední čtyři týdny a
                 věkové skupiny za posledních 52 týdnů na 100 tis. obyvatel
                 (scrapers/uzis_isin_weekly.py)
  isin_overview  z ročního CSV: deset nejčastějších diagnóz v každém roce
                 s podílem EWS, věkové skupiny posledního roku na 100 tis.
                 obyvatel a věta ke krajské mapě (scrapers/uzis_isin.py)
  isin_groups    z ročního CSV pro stránky tematických skupin: roční počty
                 každé diagnózy skupiny s částí z EWS, věk skupiny na 100 tis.
                 obyvatel a u „Ostatních“ deset největších diagnóz po letech

Co tyhle bloky NESMÍ tvrdit — hlídají to testy:
  - od července 2025 vzniká část záznamů z hlášení EWS a jejich podíl dál roste.
    Když u diagnózy přesáhne EWS_COMPARABLE_MAX, počet se s předchozími roky
    NESROVNÁVÁ: místo faktu o srovnání vznikne upozornění. Ani celkový počet, ani
    počet bez EWS není s historií srovnatelný, protože část hlášení se do EWS
    přelila ze starého kanálu (methodology_changes.yaml, zoster-vykazovani-2025-07);
  - počet hlášených případů není počet nemocných;
  - krátká jména diagnóz (curated/diagnosis_names.yaml) nesmějí zužovat kód —
    úřední název jde do JSON vždy s nimi.

Fakta a komentář skládá `facts.py`, stejně jako u respiračních a genomických
bloků: text jde později nahradit výstupem jazykového modelu beze změny stránky.

Použití:
    python scripts/compute_isin.py [--force]

Běží po generate_json.py: větu ke krajské mapě bere z hotového
isin_regional_incidence.json, aby čísla v textu byla přesně ta v mapě.

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_meta
import labels_en
from facts import NBSP, Facts, fmt_int, plural_cs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

WEEKLY_FILE = DATA_DIR / "isin" / "isin_tydenni.csv"
WEEKLY_STATE = DATA_DIR / "isin" / "isin_tydenni_stav.json"
ISIN_FILE = DATA_DIR / "isin" / "isin_infekcni_nemoci.csv"
POP_AGE_FILE = DATA_DIR / "csu" / "population_age.csv"
NAMES_FILE = ROOT / "curated" / "diagnosis_names.yaml"

# Podíl záznamů z EWS (v procentech), od kterého se počet nesrovnává s předchozími
# roky. Deset procent: pod ním je posun menší než běžné meziroční kolísání řad,
# nad ním už může být celý „nárůst“ jen změna kanálu hlášení.
EWS_COMPARABLE_MAX = 10.0

TOP_N = 10
AGE_WINDOW_WEEKS = 52
TOTAL = "Celkem"
# Chřipka má vlastní, podrobnější zdroj (SZÚ) a vlastní panely.
EXCLUDED = {"J10"}

MONTHS_CS = ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září",
             "října", "listopadu", "prosince"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]

# Tematické skupiny (generate_json.DISEASE_GROUPS) anglicky — stejně jako v navigaci portálu.
GROUP_TITLES_EN = {
    "childhood_airborne": "Childhood/airborne", "gastrointestinal": "Gastrointestinal/foodborne",
    "skin_contact": "Skin/contact", "vector_animal": "Tick- and animal-borne", "hepatitis": "Hepatitis",
    "sti": "Sexually transmitted", "rare_severe": "Rare severe",
}
# Meziroční změna stojí za větu, když je aspoň o polovinu a nejde o jednotky případů.
CHANGE_MIN_RATIO = 1.5
CHANGE_MIN_CASES = 100

NUTS3_SHORT = {
    "CZ010": "Praha", "CZ020": "Středočeský", "CZ031": "Jihočeský", "CZ032": "Plzeňský",
    "CZ041": "Karlovarský", "CZ042": "Ústecký", "CZ051": "Liberecký", "CZ052": "Královéhradecký",
    "CZ053": "Pardubický", "CZ063": "Vysočina", "CZ064": "Jihomoravský", "CZ071": "Olomoucký",
    "CZ072": "Zlínský", "CZ080": "Moravskoslezský",
}


# ── Jména, týdny, věk ────────────────────────────────────────────────────────

def _names() -> dict:
    return yaml.safe_load(NAMES_FILE.read_text(encoding="utf-8"))


def disease_name(code: str, official_cs: str, official_en: str | None = None) -> dict:
    """Krátké jméno pro čtenáře; bez záznamu v diagnosis_names.yaml úřední název."""
    curated = _names().get(code)
    if curated:
        return {"cs": curated["cs"], "en": curated["en"]}
    cs = re.sub(r"\s*\[[^\]]*\]", "", official_cs).strip()
    return {"cs": cs, "en": official_en or labels_en.diagnosis(code) or cs}


def _mid(name: str) -> str:
    """Jméno diagnózy uprostřed věty: malé počáteční písmeno, zkratky a vlastní jména zůstanou."""
    if len(name) > 1 and name[1].islower() and not name.startswith("Lyme"):
        return name[0].lower() + name[1:]
    return name


def region_name(code: str) -> dict:
    short = NUTS3_SHORT[code]
    cs = short if code == "CZ010" else f"Kraj {short}" if code == "CZ063" else f"{short} kraj"
    return {"cs": cs, "en": labels_en.region(code, full=True) or cs}


def cases(n: int) -> tuple[str, str]:
    """„511 hlášených případů“ / “511 reported cases” se správným tvarem pro 0 a 1."""
    if n == 0:
        return "žádný hlášený případ", "no reported cases"
    cs = plural_cs(n, "hlášený případ", "hlášené případy", "hlášených případů")
    return f"{fmt_int(n, 'cs')}{NBSP}{cs}", f"{fmt_int(n, 'en')} reported case{'' if n == 1 else 's'}"


def week_range(year: int, week: int, lang: str, with_year: bool = False) -> str:
    """ISO týden jako data: „9.–15. listopadu“ / “9–15 November”. Čísla týdnů zůstávají v datech."""
    start = date.fromisocalendar(year, week, 1)
    end = start + timedelta(days=6)
    months = MONTHS_CS if lang == "cs" else MONTHS_EN

    def day(d: date) -> str:
        return f"{d.day}.{NBSP}{months[d.month - 1]}" if lang == "cs" else f"{d.day}{NBSP}{months[d.month - 1]}"

    if start.month == end.month:
        text = f"{start.day}.–{day(end)}" if lang == "cs" else f"{start.day}–{day(end)}"
    else:
        text = f"{day(start)} – {day(end)}"
    return f"{text}{NBSP}{end.year}" if with_year else text


def age_label(lo: int, hi: int | None) -> dict:
    """Věková skupina v jednom zápisu pro oba zdroje: „0 let“, „1–4 roky“, „75+ let“."""
    if hi is None:
        cs = f"{lo}+ let"
    elif lo == hi:
        cs = f"{lo} let" if lo != 1 else "1 rok"
    else:
        cs = f"{lo}–{hi} {'roky' if hi <= 4 else 'let'}"
    return {"cs": cs, "en": labels_en.translate(cs) or cs}


def weekly_age_bounds(label: str) -> tuple[int, int | None]:
    """Skupiny týdenního souhrnu: „0 let“, „1 až 4 roky“, „75 a více let“."""
    m = re.match(r"^(\d+) až (\d+) ", label)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+) a více ", label)
    if m:
        return int(m.group(1)), None
    m = re.match(r"^(\d+) (?:let|rok)", label)
    if m:
        return int(m.group(1)), int(m.group(1))
    raise ValueError(f"neznámá věková skupina v týdenním souhrnu: {label!r}")


def isin_age_bounds(code) -> tuple[int, int | None]:
    """Kód věku v ISIN 66AAABBB: od AAA do BBB let; BBB = 999 je otevřený konec."""
    s = str(int(code))
    lo, hi = int(s[2:5]), int(s[5:8])
    return lo, None if hi >= 999 else hi


def load_population_age() -> pd.DataFrame | None:
    if not POP_AGE_FILE.exists():
        return None
    return pd.read_csv(POP_AGE_FILE)


def population(pop: pd.DataFrame, year: int, lo: int, hi: int | None) -> tuple[int, int]:
    """Počet obyvatel ČR ve věku lo–hi; za `year`, nebo za nejnovější dostupný rok. Vrací (počet, rok)."""
    years = set(pop["rok"])
    use = year if year in years else max(years)
    rows = pop[(pop["rok"] == use) & (pop["vek"] >= lo)]
    if hi is not None:
        rows = rows[rows["vek"] <= hi]
    return int(rows["pocet"].sum()), int(use)


def share(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _pct(f: Facts, name: str, x: float):
    # Do deseti procent jedno desetinné místo, výš celá čísla — víc přesnosti data nemají.
    f.pct(name, x, decimals=0 if x >= 10 else 1)


# ── Týdenní blok ─────────────────────────────────────────────────────────────

def _disease_block(code: str, g: pd.DataFrame, ages: pd.DataFrame, state: dict,
                   year: int, week: int, pop: pd.DataFrame | None) -> dict:
    g = g.sort_values(["rok", "tyden"])
    by_year = {int(y): x for y, x in g.groupby("rok")}
    cur = by_year[year]
    n_ytd, ews_ytd = int(cur["pocet"].sum()), int(cur["pocet_ews"].sum())
    ews_share = share(ews_ytd, n_ytd)
    n_wk = int(cur["pocet"].iloc[-1])
    tail = g["pocet"].tolist()[-8:]
    n_4, n_4prev = sum(tail[-4:]), sum(tail[:-4])
    same_weeks = {str(y): int(x[x["tyden"] <= week]["pocet"].sum()) for y, x in by_year.items() if y != year}
    comparable = ews_share < EWS_COMPARABLE_MAX

    source = state["diagnozy"].get(code, {})
    name = disease_name(code, source.get("cs", code), source.get("en"))

    f = Facts()
    f.text("name", name["cs"], name["en"])
    f.val("wk", None, week_range(year, week, "cs"), week_range(year, week, "en"), f"{year}-W{week:02d}")
    f.val("year", year, str(year), str(year))
    f.val("cases_wk", n_wk, *cases(n_wk), f"{year}-W{week:02d}")
    f.val("cases_ytd", n_ytd, *cases(n_ytd))
    f.count("n_ytd", n_ytd)
    f.val("cases_4", n_4, *cases(n_4))
    f.count("n_4", n_4)
    f.count("n_4prev", n_4prev)
    _pct(f, "ews_share", ews_share)

    f.fact("wk_last", "measurement",
           "Týden {wk} – {name}: {cases_wk} v celé ČR.",
           "Week of {wk} – {name}: {cases_wk} in Czechia.", required=True)
    f.fact("ytd", "measurement",
           "Od začátku roku {year} do týdne {wk}: {cases_ytd}.",
           "From the start of {year} to the week of {wk}: {cases_ytd}.", required=True)
    f.fact("last4", "measurement",
           "Poslední čtyři týdny: {cases_4}; čtyři týdny před nimi: {n_4prev}.",
           "The last four weeks: {cases_4}; the four weeks before them: {n_4prev}.")

    f.say("{name}: v týdnu {wk} {cases_wk}.",
          "{name}: {cases_wk} in the week of {wk}.", ["wk_last"])
    f.say("Od začátku roku {year} celkem {n_ytd}.",
          "Since the start of {year}: {n_ytd} in total.", ["ytd"])
    f.say("Za poslední čtyři týdny {n_4}, za čtyři týdny před nimi {n_4prev}.",
          "The last four weeks had {n_4}, the four weeks before them {n_4prev}.", ["last4"], optional=True)

    prev_year = year - 1
    if comparable and str(prev_year) in same_weeks:
        n_prev = same_weeks[str(prev_year)]
        f.val("prev_year", prev_year, str(prev_year), str(prev_year))
        f.count("n_prev", n_prev)
        direction = (("víc", "more") if n_ytd > n_prev else ("méně", "fewer") if n_ytd < n_prev
                     else ("stejně", "the same number"))
        f.text("direction", *direction)
        f.fact("prev", "measurement",
               "Stejné týdny roku {prev_year}: {n_prev} hlášených případů; letos je jich {direction}.",
               "The same weeks of {prev_year}: {n_prev} reported cases; this year has {direction}.",
               required=True)
        f.say("Za stejné týdny roku {prev_year} jich bylo {n_prev}, letos je jich tedy {direction}.",
              "The same weeks of {prev_year} had {n_prev}, so this year has {direction}.", ["prev"])
    else:
        f.fact("ews_break", "caveat",
               "Z letošních záznamů vzniklo {ews_share} z hlášení EWS, které se používá od července 2025. "
               "Počty proto nelze srovnávat s předchozími roky: vyšší číslo může být změnou hlášení, "
               "ne vyšší nemocností.",
               "Of this year's records, {ews_share} came from EWS reports, in use since July 2025. "
               "The counts therefore cannot be compared with previous years: a higher figure may be "
               "a change in reporting, not more illness.", required=True)
        f.say("Z letošních záznamů vzniklo {ews_share} z hlášení EWS, které se používá od července 2025; "
              "s předchozími roky proto počty srovnávat nelze.",
              "Of this year's records, {ews_share} came from EWS reports, in use since July 2025; "
              "the counts therefore cannot be compared with previous years.", ["ews_break"])

    block = {
        "code": code,
        "group": group_of_code().get(code[:3]),
        "name": name,
        "official": {"cs": source.get("cs", code), "en": source.get("en") or source.get("cs", code)},
        "weeks": {str(y): {"n": x["pocet"].astype(int).tolist(), "ews": x["pocet_ews"].astype(int).tolist()}
                  for y, x in by_year.items()},
        "ytd": {"n": n_ytd, "ews": ews_ytd, "ews_share": ews_share, "same_weeks": same_weeks},
        "last_week": {"n": n_wk, "ews": int(cur["pocet_ews"].iloc[-1])},
        "last4": {"n": n_4, "previous": n_4prev},
        "comparable": comparable,
        **f.block(),
    }

    # Věk: posledních 52 týdnů, ať je to celý rok a přitom až do minulého týdne.
    if pop is not None:
        groups = []
        for label, a in ages.groupby("vek", sort=False):
            lo, hi = weekly_age_bounds(label)
            n = int(a["pocet"].sum())
            people, _ = population(pop, year, lo, hi)
            groups.append({"lo": lo, **age_label(lo, hi), "n": n,
                           "rate": round(n / people * 100_000, 1) if people else None})
        block["age"] = [{k: v for k, v in grp.items() if k != "lo"}
                        for grp in sorted(groups, key=lambda grp: grp["lo"])]
    return block


def build_weekly() -> dict | None:
    if not WEEKLY_FILE.exists() or not WEEKLY_STATE.exists():
        print("  [isin_weekly] týdenní souhrn ÚZIS není stažený — přeskakuji")
        return None
    state = json.loads(WEEKLY_STATE.read_text(encoding="utf-8"))
    df = pd.read_csv(WEEKLY_FILE)
    cz = df[df["kraj_kod"] == "CZ"]
    total = cz[cz["vek"] == TOTAL]
    year = int(total["rok"].max())
    week = int(total[total["rok"] == year]["tyden"].max())
    pop = load_population_age()

    # Okno pro věk: posledních 52 dvojic (rok, týden), stejné pro všechny diagnózy.
    keys = total[["rok", "tyden"]].drop_duplicates().sort_values(["rok", "tyden"]).tail(AGE_WINDOW_WEEKS)
    window = cz[cz["vek"] != TOTAL].merge(keys, on=["rok", "tyden"])
    first = keys.iloc[0]

    diseases = [
        _disease_block(code, g, window[window["diagnoza"] == code], state, year, week, pop)
        for code, g in total.groupby("diagnoza")
    ]
    diseases.sort(key=lambda d: -d["ytd"]["n"])

    out = {
        # Den, ke kterému zdroj data uzavřel — ne den běhu pipeline.
        "posledni_datum": state["stav_k"][:10],
        "fetched_at": state.get("fetched_at"),
        "source_folder": state["slozka"],
        "year": year,
        "week": f"{year}-W{week:02d}",
        "period": {"cs": f"Týden {week_range(year, week, 'cs', with_year=True)}",
                   "en": f"Week of {week_range(year, week, 'en', with_year=True)}"},
        "years": sorted(int(y) for y in total["rok"].unique()),
        "ews_threshold": EWS_COMPARABLE_MAX,
        "diseases": diseases,
    }
    if pop is not None:
        _, pop_year = population(pop, year, 0, None)
        start = date.fromisocalendar(int(first["rok"]), int(first["tyden"]), 1)
        end = date.fromisocalendar(year, week, 1) + timedelta(days=6)
        out["age_window"] = {
            "weeks": AGE_WINDOW_WEEKS,
            "from": f"{int(first['rok'])}-W{int(first['tyden']):02d}",
            "to": out["week"],
            "population_year": pop_year,
            "label": {
                "cs": f"{start.day}.{NBSP}{start.month}.{NBSP}{start.year} – "
                      f"{end.day}.{NBSP}{end.month}.{NBSP}{end.year}",
                "en": f"{start.day}{NBSP}{MONTHS_EN[start.month - 1]} {start.year} – "
                      f"{end.day}{NBSP}{MONTHS_EN[end.month - 1]} {end.year}",
            },
        }
    return out


# ── Roční přehled ────────────────────────────────────────────────────────────

def _ranking(df: pd.DataFrame) -> dict:
    """Rok → deset nejčastějších diagnóz toho roku, každá s částí z EWS a s počtem o rok dřív."""
    official = df.drop_duplicates("diagnoza").set_index("diagnoza")["diagnoza_nazev"].to_dict()
    yearly = df.groupby(["rok", "diagnoza"])[["pocet_pripadu", "EWS"]].sum()
    by_year = {}
    for y in sorted(df["rok"].unique(), reverse=True):
        rows = yearly.loc[y].sort_values("pocet_pripadu", ascending=False).head(TOP_N)
        entries = []
        for code, r in rows.iterrows():
            n, ews = int(r["pocet_pripadu"]), int(r["EWS"])
            prev = yearly["pocet_pripadu"].get((y - 1, code))
            entries.append({
                "code": code,
                "name": disease_name(code, official[code]),
                "official": {"cs": official[code], "en": labels_en.diagnosis(code) or official[code]},
                "n": n, "ews": ews, "ews_share": share(ews, n),
                "previous": None if prev is None else int(prev),
            })
        by_year[str(int(y))] = entries
    return by_year


def _ews_caveat(f: Facts, affected: list[dict]):
    """Upozornění, že u vyjmenovaných diagnóz může být nárůst jen změnou hlášení."""
    f.pct("ews_min", EWS_COMPARABLE_MAX, decimals=0)
    f.text("ews_names",
           ", ".join(_mid(e["name"]["cs"]) for e in affected),
           ", ".join(_mid(e["name"]["en"]) for e in affected))
    f.fact("ews_top", "caveat",
           "Diagnózy, u kterých v roce {year} vzniklo aspoň {ews_min} záznamů z hlášení EWS "
           "(používá se od července 2025): {ews_names}. Nárůst proti předchozím rokům tu může být "
           "změnou hlášení, ne vyšší nemocností.",
           "Diagnoses for which at least {ews_min} of the records of {year} came from EWS reports "
           "(in use since July 2025): {ews_names}. A rise against previous years may here be a change "
           "in reporting, not more illness.", required=True)
    f.say("U části diagnóz ({ews_names}) vzniklo aspoň {ews_min} záznamů z hlášení EWS zavedeného "
          "v červenci 2025. Nárůst proti předchozím rokům tu může být změnou hlášení, ne vyšší nemocností.",
          "For some diagnoses ({ews_names}), at least {ews_min} of the records came from EWS reports, "
          "introduced in July 2025. A rise against previous years may here be a change in reporting, "
          "not more illness.", ["ews_top"])


def _top(df: pd.DataFrame, year: int) -> dict:
    by_year = _ranking(df)
    last = by_year[str(year)]
    first, affected = last[0], [e for e in last if e["ews_share"] >= EWS_COMPARABLE_MAX]

    f = Facts()
    f.val("year", year, str(year), str(year))
    f.text("top_name", _mid(first["name"]["cs"]), _mid(first["name"]["en"]))
    f.val("cases_top", first["n"], *cases(first["n"]))
    f.count("top_n", len(last))
    f.fact("top1", "measurement",
           "Rok {year} – nejčastěji hlášená diagnóza: {top_name}, {cases_top}.",
           "Year {year} – the most frequently reported diagnosis: {top_name}, {cases_top}.", required=True)
    f.fact("horizon", "meta",
           "Otevřená data ISIN končí rokem {year} a obnovují se jednou ročně.",
           "The open ISIN data end with {year} and are updated once a year.")
    f.say("Nejčastěji hlášená diagnóza roku {year}: {top_name} ({cases_top}).",
          "The most frequently reported diagnosis of {year}: {top_name} ({cases_top}).", ["top1"])
    if affected:
        _ews_caveat(f, affected)
    return {"period": {"cs": f"Rok {year}", "en": f"Year {year}"},
            "years": list(by_year), "by_year": by_year, **f.block()}


def _age(df: pd.DataFrame, year: int, pop: pd.DataFrame | None, ages: list | None = None) -> dict | None:
    """
    Věkové skupiny roku `year` na 100 tis. obyvatel. `ages` = kódy všech věkových
    skupin zdroje: skupina, ve které výběr žádný případ nemá, se ukáže s nulou —
    jinak by z grafu zmizela a čtenář by nepoznal, že tam nic není.
    """
    if pop is None:
        print("  [isin] chybí obyvatelstvo podle věku (ČSÚ) — blok věku vynechávám")
        return None
    cur = df[df["rok"] == year]
    if cur.empty:
        return None
    official = cur.drop_duplicates("diagnoza").set_index("diagnoza")["diagnoza_nazev"].to_dict()
    groups, pop_year = [], None
    for code in (ages if ages is not None else sorted(cur["vek_kod"].unique())):
        a = cur[cur["vek_kod"] == code]
        lo, hi = isin_age_bounds(code)
        n = int(a["pocet_pripadu"].sum())
        people, pop_year = population(pop, year, lo, hi)
        top = None
        if n:
            top_code = a.groupby("diagnoza")["pocet_pripadu"].sum().idxmax()
            top = disease_name(top_code, official[top_code])
        groups.append({"lo": lo, **age_label(lo, hi), "n": n,
                       "rate": round(n / people * 100_000, 1) if people else None, "top": top})
    groups.sort(key=lambda g: g["lo"])
    rated = [g for g in groups if g["rate"] is not None]
    if not rated:
        return None
    hi_g, lo_g = max(rated, key=lambda g: g["rate"]), min(rated, key=lambda g: g["rate"])
    if not hi_g["n"]:
        return None

    f = Facts()
    f.val("year", year, str(year), str(year))
    f.text("age_hi", hi_g["cs"], hi_g["en"])
    f.num("rate_hi", hi_g["rate"])
    f.text("age_lo", lo_g["cs"], lo_g["en"])
    f.num("rate_lo", lo_g["rate"])
    f.text("dg_hi", _mid(hi_g["top"]["cs"]), _mid(hi_g["top"]["en"]))
    f.fact("age_high", "measurement",
           "Rok {year} – nejvíc hlášených případů na 100 tis. obyvatel stejného věku: skupina {age_hi}, {rate_hi}.",
           "Year {year} – the most reported cases per 100,000 population of the same age: "
           "age group {age_hi}, {rate_hi}.", required=True)
    f.fact("age_low", "measurement",
           "Rok {year} – nejméně hlášených případů na 100 tis. obyvatel stejného věku: skupina {age_lo}, {rate_lo}.",
           "Year {year} – the fewest reported cases per 100,000 population of the same age: "
           "age group {age_lo}, {rate_lo}.", required=True)
    f.fact("age_top_dg", "measurement",
           "Nejčastější hlášená diagnóza ve skupině {age_hi} v roce {year}: {dg_hi}.",
           "The most frequently reported diagnosis in the age group {age_hi} in {year}: {dg_hi}.")
    f.say("V roce {year} připadalo nejvíc hlášených případů na 100 tis. obyvatel stejného věku na skupinu "
          "{age_hi} ({rate_hi}), nejméně na skupinu {age_lo} ({rate_lo}).",
          "In {year} the age group {age_hi} had the most reported cases per 100,000 population of the same "
          "age ({rate_hi}), the age group {age_lo} the fewest ({rate_lo}).", ["age_high", "age_low"])
    f.say("Nejčastější diagnóza ve skupině {age_hi}: {dg_hi}.",
          "The most frequent diagnosis in the age group {age_hi}: {dg_hi}.", ["age_top_dg"], optional=True)
    return {"period": {"cs": f"Rok {year}", "en": f"Year {year}"},
            "year": year, "population_year": pop_year,
            "groups": [{k: v for k, v in g.items() if k != "lo"} for g in groups], **f.block()}


def _regions() -> dict | None:
    """Věta ke krajské mapě — z hotového JSON mapy, aby čísla v textu byla ta v mapě."""
    path = OUTPUT_DIR / "isin_regional_incidence.json"
    if not path.exists():
        print("  [isin_overview] isin_regional_incidence.json ještě není — větu ke krajům vynechávám")
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    regions = {c: v for c, v in (data.get("regions") or {}).items() if c in NUTS3_SHORT}
    if len(regions) < 2:
        return None
    year = int(data["year"])
    hi, lo = max(regions, key=regions.get), min(regions, key=regions.get)

    f = Facts()
    f.val("year", year, str(year), str(year))
    f.text("hi_name", **region_name(hi))
    f.num("hi_rate", regions[hi])
    f.text("lo_name", **region_name(lo))
    f.num("lo_rate", regions[lo])
    f.fact("reg_high", "measurement",
           "Rok {year} – nejvíc hlášených případů na 100 tis. obyvatel: {hi_name}, {hi_rate}.",
           "Year {year} – the most reported cases per 100,000 population: {hi_name}, {hi_rate}.", required=True)
    f.fact("reg_low", "measurement",
           "Rok {year} – nejméně hlášených případů na 100 tis. obyvatel: {lo_name}, {lo_rate}.",
           "Year {year} – the fewest reported cases per 100,000 population: {lo_name}, {lo_rate}.", required=True)
    f.fact("reg_caveat", "caveat",
           "Rozdíly mezi kraji můžou odrážet i to, jak důsledně se v kraji hlásí, ne jen skutečný výskyt nemocí.",
           "Differences between regions may also reflect how thoroughly a region reports, "
           "not only the true occurrence of disease.")
    f.say("Nejvíc hlášených případů na 100 tis. obyvatel za rok {year}: {hi_name} ({hi_rate}). "
          "Nejméně: {lo_name} ({lo_rate}).",
          "The most reported cases per 100,000 population in {year}: {hi_name} ({hi_rate}). "
          "The fewest: {lo_name} ({lo_rate}).", ["reg_high", "reg_low"])
    return {"period": {"cs": f"Rok {year}", "en": f"Year {year}"}, "year": year, **f.block()}


def build_overview() -> dict | None:
    if not ISIN_FILE.exists():
        print("  [isin_overview] otevřené CSV ISIN není stažené — přeskakuji")
        return None
    df = pd.read_csv(ISIN_FILE, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df = df[~df["diagnoza"].isin(EXCLUDED)]
    year = int(df["rok"].max())
    month = int(df[df["rok"] == year]["mesic"].max())
    blocks = {"top": _top(df, year), "age": _age(df, year, load_population_age()), "regions": _regions()}
    return {
        "posledni_obdobi": f"{year}-{month:02d}",
        "year": year,
        "excluded": sorted(EXCLUDED),
        "ews_threshold": EWS_COMPARABLE_MAX,
        "blocks": {k: v for k, v in blocks.items() if v},
    }


# ── Tematické skupiny ────────────────────────────────────────────────────────

def disease_groups() -> dict:
    """Skupiny z generate_json.py — jedno místo, kde je řečeno, co kam patří."""
    import generate_json
    return generate_json.DISEASE_GROUPS


def group_of_code() -> dict:
    """Tříznakový kód MKN-10 → klíč tematické skupiny (názvy skupin jsou vedené jmény diagnóz)."""
    by_name = {d["cs"]: code for code, d in labels_en._load()["diagnoses"].items()}
    return {by_name[name]: key for key, spec in disease_groups().items()
            for name in spec["diseases"] if name in by_name}


def _series(sub: pd.DataFrame, years: list[int]) -> list[dict]:
    """Roční počty každé diagnózy výběru, s částí z EWS; řazeno podle posledního roku."""
    official = sub.drop_duplicates("diagnoza").set_index("diagnoza")["diagnoza_nazev"].to_dict()
    yearly = sub.groupby(["diagnoza", "rok"])[["pocet_pripadu", "EWS"]].sum()
    out = []
    for code in official:
        n = [int(yearly["pocet_pripadu"].get((code, y), 0)) for y in years]
        ews = [int(yearly["EWS"].get((code, y), 0)) for y in years]
        out.append({"code": code, "name": disease_name(code, official[code]),
                    "official": {"cs": official[code], "en": labels_en.diagnosis(code) or official[code]},
                    "n": n, "ews": ews, "ews_share": share(ews[-1], n[-1])})
    out.sort(key=lambda d: (-d["n"][-1], d["code"]))
    return out


def _biggest_change(diseases: list[dict]) -> dict | None:
    """
    Diagnóza, u které se počet meziročně změnil nejvíc — jen mezi těmi, u kterých
    je srovnání poctivé (EWS pod mezí), změna je aspoň o polovinu a nejde
    o jednotky případů. Mezi takovými rozhoduje rozdíl v počtu případů, ne poměr:
    pokles z 37 918 na 4 017 je větší zpráva než z 11 462 na 1 146.
    """
    best, best_diff = None, 0
    for d in diseases:
        now, prev = d["n"][-1], d["n"][-2] if len(d["n"]) > 1 else 0
        if d["ews_share"] >= EWS_COMPARABLE_MAX or max(now, prev) < CHANGE_MIN_CASES or min(now, prev) == 0:
            continue
        if max(now, prev) / min(now, prev) >= CHANGE_MIN_RATIO and abs(now - prev) > best_diff:
            best, best_diff = d, abs(now - prev)
    return best


def _group(key: str, title: str, sub: pd.DataFrame, year: int, years: list[int],
           pop: pd.DataFrame | None, ages: list) -> dict:
    diseases = _series(sub, years)
    first = diseases[0]
    affected = [d for d in diseases if d["ews_share"] >= EWS_COMPARABLE_MAX]
    changed = _biggest_change(diseases)

    f = Facts()
    f.val("year", year, str(year), str(year))
    f.val("prev_year", year - 1, str(year - 1), str(year - 1))
    f.text("top_name", _mid(first["name"]["cs"]), _mid(first["name"]["en"]))
    f.val("cases_top", first["n"][-1], *cases(first["n"][-1]))
    f.fact("top1", "measurement",
           "Rok {year} – nejčastěji hlášená diagnóza skupiny: {top_name}, {cases_top}.",
           "Year {year} – the most frequently reported diagnosis of the group: {top_name}, {cases_top}.",
           required=True)
    f.say("Nejčastěji hlášená diagnóza skupiny v roce {year}: {top_name} ({cases_top}).",
          "The most frequently reported diagnosis of the group in {year}: {top_name} ({cases_top}).", ["top1"])
    if changed:
        f.text("chg_name", changed["name"]["cs"], changed["name"]["en"])
        f.val("cases_chg", changed["n"][-1], *cases(changed["n"][-1]))
        f.count("n_chg_prev", changed["n"][-2])
        f.fact("change", "measurement",
               "{chg_name}: {cases_chg} v roce {year}, v roce {prev_year} jich bylo {n_chg_prev}.",
               "{chg_name}: {cases_chg} in {year}, against {n_chg_prev} in {prev_year}.")
        if changed is first:
            f.say("V roce {prev_year} jich bylo {n_chg_prev}.",
                  "In {prev_year} there were {n_chg_prev}.", ["change"], optional=True)
        else:
            f.say("{chg_name}: {cases_chg}, v roce {prev_year} jich bylo {n_chg_prev}.",
                  "{chg_name}: {cases_chg}, against {n_chg_prev} in {prev_year}.", ["change"], optional=True)
    if affected:
        _ews_caveat(f, affected)

    block = {"title": {"cs": title, "en": GROUP_TITLES_EN.get(key, title)},
             "period": {"cs": f"Rok {year}", "en": f"Year {year}"},
             "diseases": diseases, **f.block()}
    age = _age(sub, year, pop, ages)
    if age:
        block["age"] = age
    return block


def _other(sub: pd.DataFrame, year: int, years: list[int], pop: pd.DataFrame | None, ages: list) -> dict:
    """Diagnózy mimo tematické skupiny: deset největších v každém roce a součet všech."""
    by_year = _ranking(sub)
    last = by_year[str(year)]
    first = last[0]
    total = sub.groupby("rok")["pocet_pripadu"].sum()
    count = int(sub[sub["rok"] == year]["diagnoza"].nunique())

    f = Facts()
    f.val("year", year, str(year), str(year))
    f.count("k_dg", count)
    f.val("cases_all", int(total.get(year, 0)), *cases(int(total.get(year, 0))))
    f.text("top_name", _mid(first["name"]["cs"]), _mid(first["name"]["en"]))
    f.val("cases_top", first["n"], *cases(first["n"]))
    f.fact("other_total", "measurement",
           "Rok {year} – diagnózy mimo tematické skupiny: {k_dg} diagnóz s aspoň jedním případem, dohromady {cases_all}.",
           "Year {year} – diagnoses outside the thematic groups: {k_dg} diagnoses with at least one case, "
           "{cases_all} in total.", required=True)
    f.fact("top1", "measurement",
           "Rok {year} – nejčastější z nich: {top_name}, {cases_top}.",
           "Year {year} – the most frequent of them: {top_name}, {cases_top}.", required=True)
    f.say("Mimo tematické skupiny bylo v roce {year} hlášeno {k_dg} dalších diagnóz, dohromady {cases_all}.",
          "Outside the thematic groups, {k_dg} more diagnoses were reported in {year}, {cases_all} in total.",
          ["other_total"])
    f.say("Nejčastější z nich: {top_name} ({cases_top}).",
          "The most frequent of them: {top_name} ({cases_top}).", ["top1"])
    affected = [e for e in last if e["ews_share"] >= EWS_COMPARABLE_MAX]
    if affected:
        _ews_caveat(f, affected)

    block = {"title": {"cs": "Ostatní", "en": "Other"},
             "period": {"cs": f"Rok {year}", "en": f"Year {year}"},
             "years": list(by_year), "by_year": by_year,
             "total": [int(total.get(y, 0)) for y in years], "diagnoses": count, **f.block()}
    age = _age(sub, year, pop, ages)
    if age:
        block["age"] = age
    return block


def build_groups() -> dict | None:
    if not ISIN_FILE.exists():
        print("  [isin_groups] otevřené CSV ISIN není stažené — přeskakuji")
        return None
    df = pd.read_csv(ISIN_FILE, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df = df[~df["diagnoza"].isin(EXCLUDED)]
    year = int(df["rok"].max())
    month = int(df[df["rok"] == year]["mesic"].max())
    years = sorted(int(y) for y in df["rok"].unique())
    pop = load_population_age()
    ages = sorted(df["vek_kod"].unique())

    groups, assigned = {}, set()
    for key, spec in disease_groups().items():
        assigned.update(spec["diseases"])
        sub = df[df["diagnoza_nazev"].isin(spec["diseases"])]
        if not sub.empty:
            groups[key] = _group(key, spec["title"], sub, year, years, pop, ages)
    other = df[~df["diagnoza_nazev"].isin(assigned)]
    if not other.empty:
        groups["other"] = _other(other, year, years, pop, ages)
    return {
        "posledni_obdobi": f"{year}-{month:02d}",
        "year": year,
        "years": years,
        "excluded": sorted(EXCLUDED),
        "ews_threshold": EWS_COMPARABLE_MAX,
        "groups": groups,
    }


# ── Zápis ────────────────────────────────────────────────────────────────────

def unchanged(path: Path, obj: dict) -> bool:
    """
    True, když hotový JSON říká totéž co `obj`.

    Pipeline běží každou hodinu, týdenní souhrn se mění jednou týdně a roční CSV
    jednou ročně. Bez téhle kontroly by každý běh přepsal soubor jen kvůli času
    v `meta` — hodinový commit beze změny dat.
    """
    if not path.exists():
        return False
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    old.pop("meta", None)
    return old == json.loads(json.dumps(obj, ensure_ascii=False))


def save(name: str, obj: dict, force: bool = False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    if not force and unchanged(path, obj):
        print(f"  [{name}] beze změny — nechávám, jak je (--force přepíše)")
        return
    path.write_text(json.dumps(chart_meta.attach(name, obj), ensure_ascii=False), encoding="utf-8")
    print(f"  [{name}] → {path}")


def main() -> int:
    force = "--force" in sys.argv[1:]
    for name, build in (("isin_weekly", build_weekly), ("isin_overview", build_overview),
                        ("isin_groups", build_groups)):
        obj = build()
        if obj:
            save(name, obj, force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

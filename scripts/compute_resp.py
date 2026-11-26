"""
Tři respirační bloky pro portál → resp_overview.json, resp_age.json, flu_types.json.

  resp_overview  dlaždice „co se děje tento týden“: ILI a ARI (míra na 100 tis.),
                 podíl vzorků pozitivních na chřipku a RSV, každá s krátkou řadou
                 posledních týdnů
  resp_age       ILI a ARI podle věkových skupin: běžící sezóna proti rozpětí
                 předchozích sezón ve stejném týdnu
  flu_types      týdenní záchyty chřipky podle typu a subtypu a součty po sezónách

Všechno je z ECDC ERVISS (erviss_ili_ari_cz.csv, erviss_nonsentinel_cz.csv), tedy
z dat, která pipeline už stahuje. Práh, pásmo intenzity a trend ILI se NEPOČÍTAJÍ
znovu — berou se z flu_mem.json (compute_mem.py), aby stránky nikdy neukázaly dvě
různé hodnoty téhož. Skript proto běží až po compute_mem; bez flu_mem.json
doběhne, jen dlaždice ILI nemá pásmo ani trend.

Ke každému bloku se zapisují i FAKTA a KOMENTÁŘ (skládá je `facts.py`):
  facts    hodnoty se zástupnými znaky, věty faktů cs/en, seznam povinných —
           stejný tvar jako balíčky faktů v hodnocení LLM komentářů
  comment  věty složené šablonou z těchto faktů (cs/en), u každé ID faktů, ze
           kterých vychází. Frontend text jen zobrazí. Až komentář bude psát
           model, vymění se jen `comment`; fakta, validace i stránka zůstanou.

Co šablona nesmí (a model později také ne) — hlídají to testy:
  - podíl pozitivních je podíl ze VZORKŮ, ne z lidí
  - „převažuje“ u subtypu jen při ≥ MIN_SUBTYPED subtypovaných záchytech a podílu
    ≥ LEAD_SHARE; jinak věta, že to z tak malého počtu určit nelze
  - srovnání s předchozími sezónami jen tam, kde je aspoň MIN_BAND_SEASONS sezón
  - týden se svátkem nebo prázdninami nese upozornění (hlásí se méně)

Covid v přehledu NENÍ podíl pozitivních z ERVISS: od týdne 34/2026 tam ERVISS hlásí
jiný počet vyšetřených vzorků a řada na dřívější nenavazuje (methodology_changes.yaml,
erviss-sars-cov-2-jmenovatel-2026-08) — stejné rozhodnutí jako u flu_positivity_weekly.
Dlaždice covidu ukazuje týdenní počet nově potvrzených případů podle MZČR
(covid_cases_weekly.json), jen za úplné týdny; běžící týden by vypadal jako pokles.

Sváteční týdny: curated/holidays_cz_weekly.csv (řádek za ČR a ISO týden; pracovní
dny bez výuky a státní svátky podle vyhlášky č. 16/2005 Sb., organizace školního
roku MŠMT a zákona č. 245/2000 Sb.). Tabulka končí srpnem 2027 — pro pozdější
týdny je příznak „neznámo“, ne „není sváteční“.

Použití:
    python scripts/compute_resp.py

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_meta
import generate_json as gj
import last_checked
from facts import MAX_COMMENT_WORDS, NBSP, Facts, fmt_num, fmt_pct, plural_cs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

RATES_FILE = DATA_DIR / "ecdc" / "erviss_ili_ari_cz.csv"
LAB_FILE = DATA_DIR / "ecdc" / "erviss_nonsentinel_cz.csv"
HOLIDAYS_FILE = ROOT / "curated" / "holidays_cz_weekly.csv"
FLU_MEM_FILE = OUTPUT_DIR / "flu_mem.json"
# Covid se nebere z ERVISS (viz docstring), ale z týdenních případů MZČR — a ne
# znovu z CSV, nýbrž z grafu, který z nich generate_json.py už udělal.
COVID_WEEKLY_FILE = OUTPUT_DIR / "covid_cases_weekly.json"
COVID_SUMMARY_FILE = OUTPUT_DIR / "covid_summary.json"


OVERVIEW_WEEKS = 12            # délka křivky v dlaždici (kalendářní týdny)
# Poslední dva laboratorní týdny se ještě doplňují (při prvním zveřejnění bývají
# známy zhruba tři čtvrtiny záchytů — docs/analytics/nowcasting.md).
PROVISIONAL_WEEKS = 2
# Směr u podílu pozitivních: rozdíl aspoň půl procentního bodu (pravidlo CDC FluView).
DIRECTION_MIN_PP = 0.5
# Týden se počítá jako sváteční při státním svátku v pracovní den nebo při
# aspoň dvou dnech školních prázdnin (celostátní průměr; jarní prázdniny jsou
# rozložené do šesti týdnů a pod tuhle mez se celostátně nedostanou).
HOLIDAY_MIN_SCHOOL_DAYS = 2.0

AGE_GROUPS = [                 # kód v ERVISS, popisek cs, popisek en
    ("0-4", "0–4 roky", "0–4 years"),
    ("5-14", "5–14 let", "5–14 years"),
    ("15-64", "15–64 let", "15–64 years"),
    ("65+", "65 a více let", "65 years and over"),
]
MIN_BAND_SEASONS = 3           # pod tímhle počtem sezón se rozpětí neuvádí
# Skupina je „trvale nejvyšší“, když vede aspoň v tolika týdnech z dat; pod tím
# se o tom nepíše (není to stálá vlastnost, ale stav).
STRUCTURAL_MIN_SHARE = 0.8

SUBTYPES = {                   # subtyp v ERVISS → kategorie grafu
    "A(H1)pdm09": "h1", "A(H3)": "h3", "A (unknown)": "a_unk",
    "B (unknown)": "b", "B/Vic": "b", "B/Yam": "b",
}
TYPE_CATEGORIES = [            # pořadí = pořadí vrstev ve sloupci i barev (neměnit)
    ("h1", "A(H1)pdm09", "A(H1)pdm09"),
    ("h3", "A(H3)", "A(H3)"),
    ("b", "B", "B"),
    ("a_unk", "A bez určení subtypu", "A, subtype not determined"),
]
MIN_SUBTYPED = 30              # pod tímhle počtem se podíl subtypů neuvádí
LEAD_SHARE = 0.6               # od tohohle podílu se smí napsat „převažuje“

INDICATORS = {
    "ILI": ("ili", "onemocněními podobnými chřipce (ILI)", "influenza-like illness (ILI)",
            "Onemocnění podobná chřipce (ILI)", "Influenza-like illness (ILI)"),
    "ARI": ("ari", "akutními respiračními infekcemi (ARI)", "acute respiratory infections (ARI)",
            "Akutní respirační infekce (ARI)", "Acute respiratory infections (ARI)"),
}
# Názvy dlaždic v přehledu: krátké a pro laika. „Chřipka“ jako nemocnost je ILI —
# samotná chřipka se v otevřených datech jako počet nemocných nehlásí; laboratoře
# dávají jen podíl pozitivních mezi vyšetřenými vzorky.
TILE_LABELS = {
    "ili": ("Chřipka a podobná onemocnění (ILI)", "Influenza and similar illness (ILI)"),
    "ari": ("Všechny akutní respirační infekce (ARI)", "All acute respiratory infections (ARI)"),
    "flu": ("Laboratorně potvrzená chřipka", "Laboratory-confirmed influenza"),
    "rsv": ("Laboratorně potvrzený RSV", "Laboratory-confirmed RSV"),
    "covid": ("COVID-19", "COVID-19"),
}
LEVEL_TEXT = {
    "baseline": ("pod epidemickým prahem", "below the epidemic threshold"),
    "low": ("nad epidemickým prahem, intenzita nízká", "above the epidemic threshold, low intensity"),
    "medium": ("nad epidemickým prahem, intenzita střední", "above the epidemic threshold, medium intensity"),
    "high": ("nad epidemickým prahem, intenzita vysoká", "above the epidemic threshold, high intensity"),
    "very_high": ("nad epidemickým prahem, intenzita velmi vysoká",
                  "above the epidemic threshold, very high intensity"),
}
TREND_TEXT = {
    "growing": ("roste", "growing"),
    "likely_growing": ("pravděpodobně roste", "likely growing"),
    "stable": ("pravděpodobně stabilní", "likely stable"),
    "likely_declining": ("pravděpodobně klesá", "likely declining"),
    "declining": ("klesá", "declining"),
}
DIRECTION_TEXT = {
    "higher": ("vyšší", "higher"), "lower": ("nižší", "lower"), "same": ("beze změny", "unchanged"),
}

# ── Formátování a týdny ──────────────────────────────────────────────────────

MONTHS_CS = ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září",
             "října", "listopadu", "prosince"]          # 2. pád: „2.–8. listopadu“
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]


class WeekFacts(Facts):
    """Fakta s pomocníkem pro týden — ten se do textu zapisuje datem, ne číslem."""

    def week(self, name: str, week: str):
        self.val(name, None, week_label(week, "cs"), week_label(week, "en"), week)


def monday(week: str) -> date:
    return date.fromisocalendar(int(week[:4]), int(week[6:]), 1)


def day_label(day: date, lang: str) -> str:
    """'8. listopadu' / '8 November' — bez roku, čtenář ho zná z hlavičky bloku."""
    return (f"{day.day}.{NBSP}{MONTHS_CS[day.month - 1]}" if lang == "cs"
            else f"{day.day}{NBSP}{MONTHS_EN[day.month - 1]}")


def week_label(week: str, lang: str) -> str:
    """
    '2026-W45' → '2.–8. listopadu' / '2–8 November'. Čtenář v kalendářních týdnech
    nepřemýšlí; číslo týdne zůstává jen v datech (`week`). Ve větě se používá za
    slovem „týden“: „v týdnu 2.–8. listopadu“, „the week of 2–8 November“.
    """
    start, end = monday(week), monday(week) + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}.–{day_label(end, lang)}" if lang == "cs" else f"{start.day}–{day_label(end, lang)}"
    return f"{day_label(start, lang)} – {day_label(end, lang)}"


def iso(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def shift(week: str, by: int) -> str:
    return iso(monday(week) + timedelta(weeks=by))


def season_of(week: str) -> int:
    """Rok, ve kterém sezóna začala; sezóna = týden 40 až týden 39 dalšího roku."""
    year, wk = int(week[:4]), int(week[6:])
    return year if wk >= 40 else year - 1


def season_label(start: int) -> str:
    return f"{start}/{(start + 1) % 100:02d}"


def season_axis(start: int) -> list[str]:
    """ISO týdny sezóny na ose grafu: týden 40 až 20 (včetně týdne 53, pokud ho rok má)."""
    last = date(start, 12, 28).isocalendar()[1]
    return ([f"{start}-W{w:02d}" for w in range(40, last + 1)]
            + [f"{start + 1}-W{w:02d}" for w in range(1, 21)])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Sváteční týdny ───────────────────────────────────────────────────────────

def load_holidays() -> dict[str, dict]:
    if not HOLIDAYS_FILE.exists():
        print(f"  [resp] {HOLIDAYS_FILE.name} chybí — sváteční týdny se neoznačí")
        return {}
    h = pd.read_csv(HOLIDAYS_FILE)
    return {f"{int(r.iso_year)}-W{int(r.iso_week):02d}":
            {"public": int(r.public_holidays), "school": float(r.school_days_off_excl_public)}
            for r in h.itertuples()}


def holiday_kind(week: str, table: dict) -> str | None:
    """'public' | 'school' | 'both' | '' (běžný týden) | None (tabulka týden nezná)."""
    rec = table.get(week)
    if rec is None:
        return None
    public, school = rec["public"] >= 1, rec["school"] >= HOLIDAY_MIN_SCHOOL_DAYS
    return "both" if public and school else "public" if public else "school" if school else ""


HOLIDAY_TEXT = {
    "public": ("státní svátek", "a public holiday"),
    "school": ("školní prázdniny", "school holidays"),
    "both": ("státní svátek a školní prázdniny", "a public holiday and school holidays"),
}


# ── Fakta a komentář ─────────────────────────────────────────────────────────

def _holiday_fact(f: Facts, week: str, kind: str | None, week_name: str):
    """Upozornění na sváteční týden — společné pro přehled i věk."""
    if not kind:
        return
    cs, en = HOLIDAY_TEXT[kind]
    f.text("holiday_kind", cs, en)
    f.fact("caveat_holiday", "caveat",
           "Týden {" + week_name + "} byl sváteční ({holiday_kind}); v takových týdnech se hlásí méně "
           "a hodnoty bývají nižší bez ohledu na skutečnou nemocnost.",
           "The week of {" + week_name + "} included {holiday_kind}; fewer illnesses are reported in such "
           "weeks and values tend to be lower regardless of the actual illness level.", required=True)
    f.say("Byl to sváteční týden ({holiday_kind}), kdy se hlásí méně.",
          "It was a holiday week ({holiday_kind}), when fewer illnesses are reported.",
          ["caveat_holiday"])


# ── Načtení dat ──────────────────────────────────────────────────────────────

def load_rates() -> pd.DataFrame:
    """Míry ILI/ARI: index týden, sloupce (ukazatel, věk)."""
    r = pd.read_csv(RATES_FILE)
    return r.pivot_table(index="tyden_iso", columns=["ukazatel", "vek"], values="mira_na_100k", aggfunc="first")


def load_lab() -> pd.DataFrame:
    lab = pd.read_csv(LAB_FILE)
    return lab[lab["vek"] == "total"]


def positivity(lab: pd.DataFrame, typ: str, subtyp: str) -> pd.DataFrame:
    """Týdenní záchyty, vyšetřené vzorky a podíl pozitivních (stejná pravidla jako flu_positivity_weekly)."""
    rows = lab[(lab["typ"] == typ) & (lab["subtyp"] == subtyp) & (lab["tyden_iso"] >= gj.ERVISS_POSITIVITY_FROM)]
    w = rows.pivot_table(index="tyden_iso", columns="ukazatel", values="hodnota", aggfunc="sum")
    w = w.reindex(columns=["detections", "tests"]).sort_index()
    w["detections"] = gj._detections_with_zeros(w["detections"], w["tests"])
    w["percent"] = gj._percent(w["detections"], w["tests"])
    return w


def flu_mem_state() -> dict:
    """Práh, pásmo a trend ILI z flu_mem.json; prázdné, když soubor není."""
    try:
        ili = json.loads(FLU_MEM_FILE.read_text(encoding="utf-8"))["indicators"]["ili"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        print("  [resp] flu_mem.json není k dispozici — dlaždice ILI bude bez pásma a trendu")
        return {}
    return {"week": ili["current"].get("week"), "level": ili["current"].get("level"),
            "threshold": ili["thresholds"].get("epidemic"), "trend": ili["trend"].get("current")}


def covid_weekly() -> pd.Series:
    """
    Týdenní nově potvrzené případy covidu-19 (MZČR), index ISO týden — jen úplné
    týdny. Prázdná řada, když graf není k dispozici: přehled se obejde bez covidu.
    """
    try:
        chart = json.loads(COVID_WEEKLY_FILE.read_text(encoding="utf-8"))
        labels, data = chart["labels"], chart["datasets"][0]["data"]
    except (FileNotFoundError, KeyError, IndexError, json.JSONDecodeError):
        print("  [resp] covid_cases_weekly.json není k dispozici — přehled bude bez covidu")
        return pd.Series(dtype=float)
    try:
        last_day = date.fromisoformat(json.loads(COVID_SUMMARY_FILE.read_text(encoding="utf-8"))["posledni_datum"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        last_day = None
    out = {}
    for i, (label, value) in enumerate(zip(labels, data)):
        start = date.fromisoformat(label)
        # Bez data posledního dne se poslední týden bere jako neúplný (bývá).
        done = start + timedelta(days=6) <= last_day if last_day else i < len(labels) - 1
        if done and value is not None:
            out[iso(start)] = float(value)
    return pd.Series(out, dtype=float)


# ── 1. Přehled týdne ─────────────────────────────────────────────────────────

def _series(values: pd.Series, weeks: list[str], provisional: set[str] = frozenset()) -> list[dict]:
    out = []
    for w in weeks:
        v = values.get(w)
        out.append({"week": w, "value": None if v is None or pd.isna(v) else round(float(v), 1),
                    **({"provisional": True} if w in provisional else {})})
    return out


def build_overview(rates: pd.DataFrame, lab: pd.DataFrame, holidays: dict, mem: dict,
                   covid: pd.Series | None = None) -> dict | None:
    total = {ind: rates[(ind, "total")].dropna() for ind in INDICATORS if (ind, "total") in rates.columns}
    if "ILI" not in total or total["ILI"].empty:
        print("  [resp_overview] chybí řada ILI — přeskakuji")
        return None
    pos = {"flu": positivity(lab, "Influenza", "total"), "rsv": positivity(lab, "RSV", "RSV")}
    lab_weeks = pos["flu"].index[pos["flu"]["detections"].notna()]
    wk_syn = total["ILI"].index[-1]
    wk_lab = lab_weeks[-1] if len(lab_weeks) else None
    end = max(w for w in (wk_syn, wk_lab) if w)
    weeks = [shift(end, -i) for i in range(OVERVIEW_WEEKS - 1, -1, -1)]
    provisional = set(lab_weeks[-PROVISIONAL_WEEKS:])
    kind = holiday_kind(wk_syn, holidays)
    # Týden PO svátečním: nárůst proti němu je návrat hlášení k normálu, ne nárůst
    # nemocnosti — stejné upozornění, jen obrácené.
    kind_prev = None if kind else holiday_kind(shift(wk_syn, -1), holidays)

    f = WeekFacts()
    f.week("wk_syn", wk_syn)
    tiles = []

    for ind, (key, long_cs, long_en, label_cs, label_en) in INDICATORS.items():
        if ind not in total:
            continue
        s = total[ind]
        now, prev_week = float(s.iloc[-1]), shift(s.index[-1], -1)
        prev = float(s[prev_week]) if prev_week in s.index else None
        tile = {"id": key, "label": TILE_LABELS[key][0], "label_en": TILE_LABELS[key][1], "unit": "per_100k",
                "week": s.index[-1], "value": round(now, 1),
                "prev_week": prev_week if prev is not None else None,
                "prev": None if prev is None else round(prev, 1),
                "change_pct": None if not prev else round((now - prev) / prev * 100, 1),
                "holiday": kind or None, "prev_holiday": kind_prev or None, "series": _series(s, weeks)}
        f.num(f"{key}_now", now, s.index[-1])
        if key == "ili":
            if mem.get("week") == wk_syn and mem.get("level") in LEVEL_TEXT and mem.get("threshold"):
                tile.update(level=mem["level"], threshold=round(mem["threshold"], 1))
                f.num("thr_epi", mem["threshold"])
                f.text("level", *LEVEL_TEXT[mem["level"]])
                f.fact("ili_now", "measurement",
                       "Týden {wk_syn} – nemocnost onemocněními podobnými chřipce (ILI): {ili_now} na 100 tis. obyvatel.",
                       "Week of {wk_syn} – influenza-like illness (ILI) rate: {ili_now} per 100,000 population.", required=True)
                f.fact("level_now", "category",
                       "Týden {wk_syn} – ILI: {level}; epidemický práh je {thr_epi} na 100 tis. obyvatel.",
                       "Week of {wk_syn} – ILI: {level}; the epidemic threshold is {thr_epi} per 100,000.", required=True)
                f.say("V týdnu {wk_syn} byla nemocnost onemocněními podobnými chřipce (ILI) {ili_now} na 100 tis. "
                      "obyvatel, {level} (práh {thr_epi}).",
                      "In the week of {wk_syn}, the rate of influenza-like illness (ILI) was {ili_now} per 100,000 "
                      "population, {level} (threshold {thr_epi}).", ["ili_now", "level_now"])
                trend = mem.get("trend")
                if trend and trend.get("category") in TREND_TEXT:
                    tile["trend"] = {"category": trend["category"], "p_growth": trend.get("p_growth")}
                    f.text("trend", *TREND_TEXT[trend["category"]])
                    f.pct("p_growth", trend["p_growth"] * 100, wk_syn, 0)
                    f.fact("trend", "estimate",
                           "Trend ILI za poslední tři týdny k týdnu {wk_syn}: {trend} (pravděpodobnost růstu "
                           "{p_growth}); jde o odhad.",
                           "ILI trend over the last three weeks as of the week of {wk_syn}: {trend} (probability "
                           "of growth {p_growth}); this is an estimate.", required=True)
                    f.say("Trend za poslední tři týdny je podle odhadu: {trend} (pravděpodobnost růstu {p_growth}).",
                          "The trend over the last three weeks is estimated as {trend} (probability of growth "
                          "{p_growth}).", ["trend"])
            else:
                f.fact("ili_now", "measurement",
                       "Týden {wk_syn} – nemocnost onemocněními podobnými chřipce (ILI): {ili_now} na 100 tis. obyvatel.",
                       "Week of {wk_syn} – influenza-like illness (ILI) rate: {ili_now} per 100,000 population.", required=True)
                f.say("V týdnu {wk_syn} byla nemocnost onemocněními podobnými chřipce (ILI) {ili_now} na 100 tis. "
                      "obyvatel.",
                      "In the week of {wk_syn}, the rate of influenza-like illness (ILI) was {ili_now} per 100,000 "
                      "population.", ["ili_now"])
        else:
            f.fact(f"{key}_now", "measurement",
                   "Týden {wk_syn} – nemocnost " + long_cs + ": {" + key + "_now} na 100 tis. obyvatel.",
                   "Week of {wk_syn} – rate of " + long_en + ": {" + key + "_now} per 100,000 population.")
        tiles.append(tile)

    _holiday_fact(f, wk_syn, kind, "wk_syn")
    if kind_prev and shift(wk_syn, -1) in total["ILI"].index:
        f.week("wk_prev", shift(wk_syn, -1))
        f.text("holiday_kind", *HOLIDAY_TEXT[kind_prev])
        f.fact("caveat_holiday_prev", "caveat",
               "Týden {wk_prev} byl sváteční ({holiday_kind}) a hlásilo se v něm méně; rozdíl mezi ním a týdnem "
               "{wk_syn} proto nelze vykládat jako změnu nemocnosti.",
               "The week of {wk_prev} was a holiday week ({holiday_kind}) with fewer reports; the difference "
               "between it and the week of {wk_syn} therefore cannot be read as a change in illness.", required=True)
        f.say("Předchozí týden byl sváteční ({holiday_kind}) a hlásilo se méně; rozdíl proti němu proto není "
              "změna nemocnosti.",
              "The previous week was a holiday week ({holiday_kind}) with fewer reports, so the difference from "
              "it is not a change in illness.", ["caveat_holiday_prev"])

    for key, w in pos.items():
        done = w.index[w["detections"].notna()]
        if not len(done):
            continue
        wk, (label_cs, label_en) = done[-1], TILE_LABELS[key]
        row, prev_week = w.loc[wk], shift(done[-1], -1)
        # Sloupec DataFrame dělá z None NaN — zpátky na None, ať se „neuvádí se“ pozná.
        now = None if pd.isna(row["percent"]) else float(row["percent"])
        prev = w.loc[prev_week, "percent"] if prev_week in w.index else None
        prev = None if prev is None or pd.isna(prev) else float(prev)
        direction = None
        if now is not None and prev is not None:
            direction = ("higher" if now - prev >= DIRECTION_MIN_PP else
                         "lower" if prev - now >= DIRECTION_MIN_PP else "same")
        tiles.append({"id": f"{key}_pos", "label": label_cs, "label_en": label_en, "unit": "percent",
                      "week": wk, "value": now, "detections": int(row["detections"]), "tests": int(row["tests"]),
                      "min_tests": gj.POSITIVITY_MIN_TESTS,
                      "prev_week": prev_week if direction else None,
                      "prev": prev if direction else None, "direction": direction, "provisional": True,
                      "series": _series(w["percent"], weeks, provisional)})
        if key != "flu":
            if now is not None:
                f.week("wk_rsv", wk)
                f.pct("rsv_pos", now, wk)
                f.count("rsv_tests", int(row["tests"]), wk)
                f.fact("rsv_pos", "measurement",
                       "Týden {wk_rsv} – podíl vzorků pozitivních na RSV: {rsv_pos} z {rsv_tests} vyšetřených.",
                       "Week of {wk_rsv} – share of specimens positive for RSV: {rsv_pos} of {rsv_tests} tested.")
            continue
        f.week("wk_lab", wk)
        f.count("flu_tests", int(row["tests"]), wk)
        # Stejný týden jako u ILI se ve větě neopakuje datem — dvě data vedle sebe se pletou.
        if wk == wk_syn:
            f.text("flu_when", "ve stejném týdnu", "in the same week")
        else:
            f.text("flu_when", "v týdnu " + week_label(wk, "cs"), "in the week of " + week_label(wk, "en"))
        if now is None:
            f.fact("caveat_low_n", "caveat",
                   "Týden {wk_lab}: vyšetřeno bylo jen {flu_tests} vzorků, podíl pozitivních na chřipku se "
                   "proto neuvádí.",
                   "Week of {wk_lab}: only {flu_tests} specimens were tested, so the share positive for "
                   "influenza is not given.", required=True)
            f.say("Laboratoře {flu_when} vyšetřily jen {flu_tests} vzorků, podíl pozitivních na chřipku se "
                  "proto neuvádí.",
                  "Laboratories tested only {flu_tests} specimens {flu_when}, so the share positive for "
                  "influenza is not given.", ["caveat_low_n"])
            continue
        f.pct("flu_pos", now, wk)
        f.count("flu_det", int(row["detections"]), wk)
        f.fact("flu_pos", "measurement",
               "Týden {wk_lab} – podíl vzorků pozitivních na chřipku: {flu_pos} ({flu_det} z {flu_tests} "
               "vyšetřených).",
               "Week of {wk_lab} – share of specimens positive for influenza: {flu_pos} ({flu_det} of {flu_tests} "
               "tested).", required=True)
        cs = "Podíl vzorků pozitivních na chřipku byl {flu_when} {flu_pos} ({flu_det} z {flu_tests} vyšetřených)"
        en = "The share of specimens positive for influenza {flu_when} was {flu_pos} ({flu_det} of {flu_tests} tested)"
        used = ["flu_pos"]
        if direction:
            f.week("wk_lab_prev", prev_week)
            f.pct("flu_pos_prev", prev, prev_week)
            f.text("flu_dir", *DIRECTION_TEXT[direction])
            f.fact("flu_dir", "category",
                   "Podíl vzorků pozitivních na chřipku: týden {wk_lab} proti týdnu {wk_lab_prev} "
                   "({flu_pos_prev}) {flu_dir}.",
                   "Share of specimens positive for influenza: week of {wk_lab} versus week of {wk_lab_prev} "
                   "({flu_pos_prev}) {flu_dir}.")
            cs += ", proti předchozímu týdnu {flu_dir}"
            en += ", {flu_dir} than the week before" if direction != "same" else ", {flu_dir} from the week before"
            used.append("flu_dir")
        f.say(cs + ".", en + ".", used)
        f.fact("caveat_provisional", "caveat",
               "Laboratorní údaje za týden {wk_lab} jsou předběžné a ještě se doplňují. Podíl pozitivních tím "
               "systematicky vychýlen není.",
               "Laboratory figures for the week of {wk_lab} are provisional and still being completed. The "
               "share of positives is not systematically biased by this.", required=True)
        f.say("Laboratorní údaje jsou předběžné.",
              "The laboratory figures are provisional.", ["caveat_provisional"])

    if covid is not None and len(covid):
        wk = covid.index[-1]
        now, prev_week = float(covid.iloc[-1]), shift(covid.index[-1], -1)
        prev = float(covid[prev_week]) if prev_week in covid.index else None
        tiles.append({"id": "covid", "label": TILE_LABELS["covid"][0], "label_en": TILE_LABELS["covid"][1],
                      "unit": "count",
                      "week": wk, "value": int(now), "prev_week": prev_week if prev is not None else None,
                      "prev": None if prev is None else int(prev),
                      "change_pct": None if not prev else round((now - prev) / prev * 100, 1),
                      "series": _series(covid, weeks)})
        f.week("wk_cov", wk)
        f.count("cov_cases", int(now), wk)
        if wk == wk_syn:
            f.text("cov_when", "ve stejném týdnu", "in the same week")
        else:
            f.text("cov_when", "v týdnu " + week_label(wk, "cs"), "in the week of " + week_label(wk, "en"))
        f.fact("cov_cases", "measurement",
               "Týden {wk_cov} – nově potvrzené případy covidu-19 podle MZČR: {cov_cases}. Počet závisí na tom, "
               "kolik se testuje.",
               "Week of {wk_cov} – newly confirmed COVID-19 cases according to the Ministry of Health: "
               "{cov_cases}. The count depends on how much testing is done.")
        f.say("Nově potvrzených případů covidu-19 bylo {cov_when} {cov_cases}.",
              "Newly confirmed COVID-19 cases {cov_when}: {cov_cases}.", ["cov_cases"], optional=True)

    f.fact("source", "meta",
           "Zdroj: hlášení praktických lékařů a laboratoří (ECDC ERVISS, WHO FluID), případy covidu-19 MZČR; "
           "práh, trend a podíly počítá Pathogen Portal.",
           "Source: reports from general practitioners and laboratories (ECDC ERVISS, WHO FluID), COVID-19 "
           "cases from the Ministry of Health; the threshold, trend and shares are computed by Pathogen Portal.")
    return {"generated_at": _now(), "week": wk_syn, "lab_week": wk_lab, "weeks": weeks,
            "provisional_weeks": sorted(provisional), "tiles": tiles, **f.block()}


# ── 2. Věkové skupiny ────────────────────────────────────────────────────────

def build_age(rates: pd.DataFrame, holidays: dict) -> dict | None:
    weeks_all = list(rates.index)
    in_season = [w for w in weeks_all if not 20 < int(w[6:]) < 40]
    if not in_season:
        print("  [resp_age] žádný týden sezóny — přeskakuji")
        return None
    current = season_of(in_season[-1])
    axis = season_axis(current)
    last_week = max(w for w in in_season if w in axis)
    past = sorted({season_of(w) for w in in_season} - {current})
    # Sezóna se počítá, jen když má data na většině osy (první sezóna ERVISS začíná až v týdnu 25).
    past = [s for s in past if sum(w in rates.index for w in season_axis(s)) >= 20]
    holiday = [holiday_kind(w, holidays) for w in axis]

    def band(col, i):
        vals = []
        for s in past:
            ax = season_axis(s)
            # Týden 53 nemá v sezónách bez něj protějšek — osa se zarovnává podle čísla týdne.
            match = [w for w in ax if w[6:] == axis[i][6:]]
            if match and match[0] in rates.index and not pd.isna(rates.loc[match[0], col]):
                vals.append(float(rates.loc[match[0], col]))
        return (round(min(vals), 1), round(max(vals), 1)) if len(vals) >= MIN_BAND_SEASONS else (None, None)

    out = {"generated_at": _now(), "season": season_label(current), "last_week": last_week,
           "weeks": axis, "holiday": holiday, "past_seasons": [season_label(s) for s in past],
           "min_band_seasons": MIN_BAND_SEASONS, "indicators": {}}
    idx = axis.index(last_week)

    for ind, (key, long_cs, long_en, label_cs, label_en) in INDICATORS.items():
        groups = []
        for code, g_cs, g_en in AGE_GROUPS + [("total", "celkem", "all ages")]:
            col = (ind, code)
            if col not in rates.columns:
                continue
            cur = [None if w not in rates.index or pd.isna(rates.loc[w, col]) else round(float(rates.loc[w, col]), 1)
                   for w in axis]
            bands = [band(col, i) for i in range(len(axis))]
            lo, hi, now = bands[idx][0], bands[idx][1], cur[idx]
            groups.append({"id": code, "label": g_cs, "label_en": g_en, "current": cur,
                           "min": [b[0] for b in bands], "max": [b[1] for b in bands],
                           # Stav v posledním týdnu proti rozpětí předchozích sezón — pro karty nad grafem.
                           "now": {"value": now, "min": lo, "max": hi,
                                   "position": None if now is None or lo is None else
                                   "above" if now > hi else "below" if now < lo else "within"}})
        ages = [g for g in groups if g["id"] != "total" and g["current"][idx] is not None]
        if not ages:
            continue
        top = max(ages, key=lambda g: g["current"][idx])

        f = WeekFacts()
        f.week("wk_now", last_week)
        f.text("age_top", top["label"], top["label_en"])
        f.num("rate_top", top["current"][idx], last_week)
        f.count("n_seasons", len(past))
        f.fact("age_top", "measurement",
               "Týden {wk_now} – nejvyšší nemocnost " + long_cs + " měla věková skupina {age_top}: {rate_top} "
               "na 100 tis. obyvatel této věkové skupiny.",
               "Week of {wk_now} – the highest rate of " + long_en + " was in the age group {age_top}: "
               "{rate_top} per 100,000 population of that age group.", required=True)
        f.say("V týdnu {wk_now} byla nejvyšší nemocnost " + long_cs + " ve věkové skupině {age_top}: {rate_top} "
              "na 100 tis. obyvatel této skupiny.",
              "In the week of {wk_now}, the highest rate of " + long_en + " was in the age group {age_top}: "
              "{rate_top} per 100,000 population of that group.", ["age_top"])

        cols = [(ind, code) for code, _, _ in AGE_GROUPS if (ind, code) in rates.columns]
        complete = rates[cols].dropna()
        share = float((complete.idxmax(axis=1) == (ind, top["id"])).mean()) if len(complete) else 0.0
        if share >= STRUCTURAL_MIN_SHARE:
            if share == 1.0:
                f.text("top_share", "ve všech týdnech", "in every week")
            else:
                f.text("top_share", f"v {fmt_pct(share * 100, 'cs', 0)} týdnů", f"in {fmt_pct(share * 100, 'en', 0)} of the weeks")
            f.fact("age_structural", "caveat",
                   "Skupina {age_top} má nejvyšší hlášenou nemocnost {top_share}, které jsou v datech; samo "
                   "o sobě to není neobvyklé.",
                   "The age group {age_top} has the highest reported rate {top_share} in the data; in itself "
                   "this is not unusual.", required=True)
            f.say("Tato skupina má nejvyšší hlášenou nemocnost {top_share}, které jsou v datech; samo o sobě "
                  "to není neobvyklé.",
                  "This group has the highest reported rate {top_share} in the data; in itself this is not "
                  "unusual.", ["age_structural"])

        lo, hi = top["min"][idx], top["max"][idx]
        if lo is not None:
            now = top["current"][idx]
            position = "above" if now > hi else "below" if now < lo else "within"
            f.num("hist_min", lo)
            f.num("hist_max", hi)
            f.text("hist_pos", *{"above": ("nad tímto rozpětím", "above this range"),
                                 "below": ("pod tímto rozpětím", "below this range"),
                                 "within": ("uvnitř tohoto rozpětí", "within this range")}[position])
            f.fact("age_hist", "measurement",
                   "Ve stejném týdnu {n_seasons} předchozích sezón byla nemocnost ve skupině {age_top} mezi "
                   "{hist_min} a {hist_max} na 100 tis.",
                   "In the same week of the {n_seasons} previous seasons, the rate in the age group {age_top} "
                   "was between {hist_min} and {hist_max} per 100,000.")
            f.fact("age_hist_pos", "category",
                   "Hodnota ve skupině {age_top} v týdnu {wk_now} je {hist_pos}.",
                   "The value in the age group {age_top} in the week of {wk_now} is {hist_pos}.")
            f.say("Ve stejném týdnu {n_seasons} předchozích sezón byla u ní nemocnost mezi {hist_min} a "
                  "{hist_max}; hodnota z tohoto týdne je {hist_pos}.",
                  "In the same week of the {n_seasons} previous seasons its rate was between {hist_min} and "
                  "{hist_max}; this week's value is {hist_pos}.", ["age_hist", "age_hist_pos"],
                  optional=True)      # srovnání se sezónami ustoupí, když je k týdnu i upozornění na svátek
            top_state = position
        else:
            top_state = None

        _holiday_fact(f, last_week, holiday[idx], "wk_now")
        f.fact("source", "meta",
               "Zdroj: hlášení praktických lékařů a pediatrů (ECDC ERVISS); rozpětí předchozích sezón počítá "
               "Pathogen Portal.",
               "Source: reports from general practitioners and paediatricians (ECDC ERVISS); the range of "
               "previous seasons is computed by Pathogen Portal.")
        out["indicators"][key] = {"label": label_cs, "label_en": label_en, "groups": groups,
                                  "top_group": top["id"], "top_position": top_state, **f.block()}
    return out if out["indicators"] else None


# ── 3. Typy a subtypy chřipky ────────────────────────────────────────────────

def build_types(lab: pd.DataFrame) -> dict | None:
    flu = lab[(lab["patogen"] == "Influenza") & (lab["ukazatel"] == "detections")]
    total = positivity(lab, "Influenza", "total")
    reported = list(total.index[total["detections"].notna()])
    if not reported:
        print("  [flu_types] žádný týden s nahlášenými výsledky — přeskakuji")
        return None

    sub = flu[flu["subtyp"] != "total"].copy()
    unknown = sorted(set(sub["subtyp"]) - set(SUBTYPES) - {"Influenza untyped"})
    if unknown:
        # Nový subtyp ve zdroji nesmí tiše zmizet ani shodit pipeline: počítá se
        # k typu (A → bez určení subtypu, B → B) a vypíše se.
        print(f"  [flu_types] ⚠ neznámé subtypy {unknown} — započteny podle typu")
    sub["cat"] = sub["subtyp"].map(SUBTYPES)
    sub.loc[sub["cat"].isna() & (sub["typ"] == "Influenza A"), "cat"] = "a_unk"
    sub.loc[sub["cat"].isna() & (sub["typ"] == "Influenza B"), "cat"] = "b"
    sub = sub.dropna(subset=["cat"])
    wide = sub.pivot_table(index="tyden_iso", columns="cat", values="hodnota", aggfunc="sum")
    wide = wide.reindex(index=reported, columns=[c for c, _, _ in TYPE_CATEGORIES]).fillna(0).astype(int)

    mismatch = int((wide.sum(axis=1) != total.loc[reported, "detections"]).sum())
    if mismatch:
        print(f"  [flu_types] ⚠ v {mismatch} týdnech součet subtypů nesedí s celkovým počtem záchytů")

    last_week = reported[-1]
    current = season_of(last_week)
    seasons = sorted({season_of(w) for w in reported})
    # První sezóna v datech začíná uprostřed (ERVISS od týdne 25/2022) — do součtů jen celé.
    seasons = [s for s in seasons if f"{s}-W40" in wide.index or s == current]
    provisional = reported[-PROVISIONAL_WEEKS:]

    def weekly(season: int) -> dict:
        axis = season_axis(season)
        rows = [({c: int(wide.loc[w, c]) for c in wide.columns} if w in wide.index else None) for w in axis]
        return {"weeks": axis, "rows": rows,
                "provisional": [w for w in provisional if w in axis] if season == current else []}

    def totals(season: int) -> dict:
        s = wide[[season_of(w) == season for w in wide.index]].sum()
        a_sub = int(s["h1"] + s["h3"])
        a_all = int(a_sub + s["a_unk"])
        enough = a_sub >= MIN_SUBTYPED
        return {"season": season_label(season), "h1": int(s["h1"]), "h3": int(s["h3"]),
                "a_unk": int(s["a_unk"]), "b": int(s["b"]), "a_total": a_all, "a_subtyped": a_sub,
                "share_subtyped": round(a_sub / a_all * 100, 1) if a_all else None,
                "share_h1": round(s["h1"] / a_sub * 100, 1) if enough else None,
                "share_h3": round(s["h3"] / a_sub * 100, 1) if enough else None,
                "complete": season != current}

    season_totals = [totals(s) for s in seasons]
    cur = season_totals[-1]
    shown = [s for s in seasons if s >= current - 1]

    f = WeekFacts()
    start = next(w for w in reported if season_of(w) == current)
    f.val("date_from", None, day_label(monday(start), "cs"), day_label(monday(start), "en"), start)
    end_day = monday(last_week) + timedelta(days=6)
    f.val("date_to", None, day_label(end_day, "cs"), day_label(end_day, "en"), last_week)
    f.text("season", season_label(current), season_label(current))
    f.val("n_a", cur["a_total"],
          f"{fmt_num(cur['a_total'], 'cs', 0)} {plural_cs(cur['a_total'], 'záchyt', 'záchyty', 'záchytů')}",
          f"{fmt_num(cur['a_total'], 'en', 0)} detection{'' if cur['a_total'] == 1 else 's'}", last_week)
    f.val("n_b", cur["b"],
          f"{fmt_num(cur['b'], 'cs', 0)} {plural_cs(cur['b'], 'záchyt', 'záchyty', 'záchytů')}",
          f"{fmt_num(cur['b'], 'en', 0)} detection{'' if cur['b'] == 1 else 's'}", last_week)
    f.fact("types_season", "measurement",
           "Od {date_from} do {date_to} laboratoře nahlásily {n_a} chřipky A a {n_b} chřipky B.",
           "From {date_from} to {date_to} laboratories reported {n_a} of influenza A and {n_b} of influenza B.",
           required=True)
    f.say("Od {date_from} do {date_to} laboratoře nahlásily {n_a} chřipky A a {n_b} chřipky B.",
          "From {date_from} to {date_to} laboratories reported {n_a} of influenza A and {n_b} of influenza B.",
          ["types_season"])

    lead = None
    if cur["a_total"]:
        f.count("n_a_sub", cur["a_subtyped"], last_week)
        f.val("n_a_of", cur["a_total"],
              f"{fmt_num(cur['a_total'], 'cs', 0)} {'záchytu' if cur['a_total'] == 1 else 'záchytů'}",
              f"{fmt_num(cur['a_total'], 'en', 0)} detection{'' if cur['a_total'] == 1 else 's'}", last_week)
        f.fact("subtyped", "measurement",
               "Subtyp byl určen u {n_a_sub} z {n_a_of} chřipky A.",
               "The subtype was determined for {n_a_sub} of {n_a_of} of influenza A.", required=True)
        share = max(cur["h1"], cur["h3"]) / cur["a_subtyped"] if cur["a_subtyped"] else 0
        if cur["a_subtyped"] >= MIN_SUBTYPED and share >= LEAD_SHARE:
            lead = "h1" if cur["h1"] >= cur["h3"] else "h3"
            f.text("lead", *[(cs, en) for c, cs, en in TYPE_CATEGORIES if c == lead][0])
            f.pct("lead_share", share * 100, last_week, 0)
            f.fact("lead_subtype", "category",
                   "Mezi subtypovanými záchyty chřipky A převažuje {lead} ({lead_share}).",
                   "Among subtyped influenza A detections {lead} predominates ({lead_share}).", required=True)
            f.say("Subtyp byl určen u {n_a_sub} z {n_a_of} chřipky A; mezi nimi převažuje {lead} ({lead_share}).",
                  "The subtype was determined for {n_a_sub} of {n_a_of} of influenza A; among these {lead} "
                  "predominates ({lead_share}).", ["subtyped", "lead_subtype"])
        elif cur["a_subtyped"] >= MIN_SUBTYPED:
            f.fact("lead_subtype", "category",
                   "Mezi subtypovanými záchyty chřipky A žádný subtyp výrazně nepřevažuje.",
                   "Among subtyped influenza A detections no subtype clearly predominates.", required=True)
            f.say("Subtyp byl určen u {n_a_sub} z {n_a_of} chřipky A; žádný subtyp mezi nimi výrazně nepřevažuje.",
                  "The subtype was determined for {n_a_sub} of {n_a_of} of influenza A; no subtype clearly "
                  "predominates among these.", ["subtyped", "lead_subtype"])
        else:
            f.fact("lead_subtype", "category",
                   "Z tak malého počtu nelze určit, který subtyp převažuje.",
                   "The number is too small to tell which subtype predominates.", required=True)
            f.say("Subtyp byl určen u {n_a_sub} z {n_a_of} chřipky A; z tak malého počtu nelze určit, který "
                  "subtyp převažuje.",
                  "The subtype was determined for {n_a_sub} of {n_a_of} of influenza A; the number is too small "
                  "to tell which subtype predominates.", ["subtyped", "lead_subtype"])

    if len(season_totals) > 1 and season_totals[-2]["a_subtyped"] >= MIN_SUBTYPED:
        prev = season_totals[-2]
        top = "h1" if prev["h1"] >= prev["h3"] else "h3"
        f.text("season_prev", prev["season"], prev["season"])
        f.count("prev_n_a", prev["a_total"])
        f.count("prev_n_sub", prev["a_subtyped"])
        f.pct("prev_p_sub", prev["share_subtyped"])
        f.count("prev_n_top", prev[top])
        f.text("prev_top", *[(cs, en) for c, cs, en in TYPE_CATEGORIES if c == top][0])
        f.fact("prev_season", "measurement",
               "V sezóně {season_prev} byl subtyp určen u {prev_n_sub} z {prev_n_a} záchytů chřipky A "
               "({prev_p_sub}); z nich {prev_n_top} bylo {prev_top}.",
               "In season {season_prev} the subtype was determined for {prev_n_sub} of {prev_n_a} influenza A "
               "detections ({prev_p_sub}); {prev_n_top} of them were {prev_top}.")
        f.say("V sezóně {season_prev} byl subtyp určen u {prev_n_sub} z {prev_n_a} záchytů chřipky A "
              "({prev_p_sub}); z nich {prev_n_top} bylo {prev_top}.",
              "In season {season_prev} the subtype was determined for {prev_n_sub} of {prev_n_a} influenza A "
              "detections ({prev_p_sub}); {prev_n_top} of them were {prev_top}.", ["prev_season"])

    f.fact("caveat_subtyping", "caveat",
           "Subtyp se určuje jen u části vzorků a jejich výběr není náhodný; podíly proto nemusí odpovídat "
           "všem nemocným.",
           "The subtype is determined for only some specimens and they are not selected at random, so the "
           "shares need not reflect all patients.", required=True)
    f.say("Subtyp se určuje jen u části vzorků a jejich výběr není náhodný.",
          "The subtype is determined for only some specimens, and they are not selected at random.",
          ["caveat_subtyping"])
    f.fact("source", "meta",
           "Zdroj: laboratorní hlášení ČR (ECDC ERVISS); součty a podíly počítá Pathogen Portal.",
           "Source: laboratory reports from Czechia (ECDC ERVISS); totals and shares are computed by "
           "Pathogen Portal.")

    return {"generated_at": _now(), "season": season_label(current), "last_week": last_week,
            "categories": [{"id": c, "label": cs, "label_en": en} for c, cs, en in TYPE_CATEGORIES],
            "weekly": {season_label(s): weekly(s) for s in shown},
            "season_totals": season_totals, "min_subtyped": MIN_SUBTYPED, "lead_share": LEAD_SHARE,
            "lead": lead, **f.block()}


# ── Zápis ────────────────────────────────────────────────────────────────────

def save(name: str, obj: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    path.write_text(json.dumps(chart_meta.attach(name, obj), ensure_ascii=False), encoding="utf-8")
    last_checked.mark_chart(name)
    print(f"  [{name}] → {path}")


def main() -> int:
    if not RATES_FILE.exists() or not LAB_FILE.exists():
        print("  [resp] data ERVISS chybí — přeskakuji")
        return 0
    rates, lab, holidays = load_rates(), load_lab(), load_holidays()
    for name, build in (("resp_overview", lambda: build_overview(rates, lab, holidays, flu_mem_state(),
                                                                 covid_weekly())),
                        ("resp_age", lambda: build_age(rates, holidays)),
                        ("flu_types", lambda: build_types(lab))):
        obj = build()
        if obj:
            save(name, obj)
    return 0


if __name__ == "__main__":
    sys.exit(main())

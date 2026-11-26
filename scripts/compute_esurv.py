"""
Denní e-surveillance respiračních infekcí (ÚZIS, NZIP 2860) → JSON pro panel portálu.

Vstup:  $DATA_DIR/uzis_esurv/*.csv a esurv_stav.json (scrapers/uzis_esurveillance.py),
        pro přepočet na obyvatele $DATA_DIR/csu/population_age.csv
Výstup: $OUTPUT_DIR/esurv_*.json — patnáct souborů:

  esurv_summary            čísla do dlaždic, datum stavu a tři bloky faktů s komentářem
                           (případy, nemocnice, očkování)
  esurv_cases              hlášené případy, klouzavý 7denní součet; čtyři řady
  esurv_cases_age          hlášené případy za 7 dní na 100 tisíc podle věku
  esurv_hosp_current       aktuálně hospitalizovaní po dnech
  esurv_hosp_new           nově přijatí do nemocnice, 7denní součet
  esurv_hosp_icu           nově přijatí na JIP nebo ARO, 7denní součet
  esurv_hosp_deaths        zemřelí za hospitalizace, 7denní součet
  esurv_hosp_age           nově přijatí za 28 dní na 100 tisíc podle věku
  esurv_map_<nemoc>        kraje: nové případy za 7 dní na 100 tisíc (čtyři soubory)
  esurv_vaccination        podané dávky od začátku sezóny (kumulativně)
  esurv_vaccination_age    podané dávky na 100 obyvatel podle věku (chřipka, COVID-19)
  esurv_lab                laboratorně potvrzené infekce, 7denní součet

Proč 7denní součty a ne denní čísla: hlášení lékařů mají silný týdenní rytmus
(neděle 22. 11. 2026 24 případů chřipky, pondělí 1 090). Denní graf by ukazoval
pracovní dobu ordinací, ne šíření nemoci. Součet za sedm dní obsahuje vždy každý
den v týdnu jednou.

Co se tu NEDĚLÁ:
  - Čísla se nesrovnávají s nemocností ILI/ARI z ERVISS ani s prahy MEM. Jsou to
    hlášení lékařů a nemocnic (EWS), ne sentinelová surveillance.
  - Počty podle očkování (esurv_ockovani_stav.csv) se nekreslí. Bez počtu
    očkovaných a neočkovaných ve stejném věku by graf vybízel k závěru, který
    z dat neplyne.
  - Kraje se berou z hodnot, které spočítal zdroj (soubor EW03), nepřepočítávají se.

Soubor se nepřepíše, když se vstup nezměnil (stejná denní složka): datum dat na
stránce pak zůstane pravdivé a do portálu nejde commit jen kvůli času běhu.

Použití:
    python scripts/compute_esurv.py            # zapíše, co se změnilo
    python scripts/compute_esurv.py --force    # přepíše všechno
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_meta
import labels_en
import last_checked
from compute_isin import MONTHS_CS, MONTHS_EN, age_label, population, unchanged
from facts import NBSP, Facts, fmt_num, plural_cs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))
ESURV_DIR = DATA_DIR / "uzis_esurv"
POP_AGE_FILE = DATA_DIR / "csu" / "population_age.csv"

# Pořadí řad ve všech grafech. ARI/ILI je poslední: je řádově větší než ostatní
# tři a stránka ji kreslí zvlášť (parametr `series` u grafu).
DISEASES = {
    "chripka": {"cs": "Chřipka", "en": "Influenza"},
    "covid19": {"cs": "COVID-19", "en": "COVID-19"},
    "rsv": {"cs": "RSV", "en": "RSV"},
    "ari_ili": {"cs": "ARI/ILI celkem", "en": "ARI/ILI, total"},
}
VACCINES = {
    "chripka": {"cs": "Proti chřipce", "en": "Against influenza"},
    "covid19": {"cs": "Proti covidu-19", "en": "Against COVID-19"},
    "rsv": {"cs": "Proti RSV", "en": "Against RSV"},
}
AGE_KEYS = ["0", "1-4", "5-9", "10-14", "15-19", "20-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"]
TOTAL = "celkem"
HOSP_AGE_DAYS = 28          # u hospitalizací je za 7 dní v jedné věkové skupině jen pár lidí
NUTS3 = ["CZ010", "CZ020", "CZ031", "CZ032", "CZ041", "CZ042", "CZ051", "CZ052", "CZ053", "CZ063",
         "CZ064", "CZ071", "CZ072", "CZ080"]
# Krátká jména krajů do mapy — stejná jako u ostatních map portálu.
NUTS3_NAMES = {
    "CZ010": "Praha", "CZ020": "Středočeský", "CZ031": "Jihočeský", "CZ032": "Plzeňský",
    "CZ041": "Karlovarský", "CZ042": "Ústecký", "CZ051": "Liberecký", "CZ052": "Královéhradecký",
    "CZ053": "Pardubický", "CZ063": "Vysočina", "CZ064": "Jihomoravský", "CZ071": "Olomoucký",
    "CZ072": "Zlínský", "CZ080": "Moravskoslezský",
}


# ── vstup ────────────────────────────────────────────────────────────────────

def load() -> dict | None:
    """Všechny tabulky zdroje a jeho stav, nebo None, když zdroj není stažený."""
    state_path = ESURV_DIR / "esurv_stav.json"
    names = {"denni": "esurv_denni.csv", "nemocnice": "esurv_nemocnice.csv", "ockovani": "esurv_ockovani.csv",
             "laboratore": "esurv_laboratore.csv", "uzemi": "esurv_uzemi.csv"}
    if not state_path.exists() or not all((ESURV_DIR / n).exists() for n in names.values()):
        return None
    data = {key: pd.read_csv(ESURV_DIR / name, dtype={"vek": str, "kategorie": str, "lau": str})
            for key, name in names.items()}
    data["state"] = json.loads(state_path.read_text(encoding="utf-8"))
    return data


def national(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Den × nemoc pro celou ČR, souvislý kalendář (chybějící den zůstane prázdný)."""
    sub = df[df["kraj_kod"] == "CZ"]
    if "vek" in sub.columns:
        sub = sub[sub["vek"] == TOTAL]
    wide = sub.pivot(index="datum", columns="nemoc", values=column)
    days = pd.date_range(wide.index.min(), wide.index.max(), freq="D").strftime("%Y-%m-%d")
    return wide.reindex(days).reindex(columns=list(DISEASES))


def rolling7(wide: pd.DataFrame) -> pd.DataFrame:
    """Součet za posledních 7 dní včetně daného dne; bez celých sedmi dní zůstane prázdno."""
    return wide.rolling(7, min_periods=7).sum()


def trim(wide: pd.DataFrame) -> pd.DataFrame:
    """Ořízne úvodní dny, kdy žádná řada nemá hodnotu nebo jsou všechny nulové."""
    has = wide.fillna(0).sum(axis=1) > 0
    return wide.loc[has.idxmax():] if has.any() else wide.iloc[0:0]


def _values(series: pd.Series, decimals: int = 0) -> list:
    return [None if pd.isna(v) else (int(v) if decimals == 0 else round(float(v), decimals)) for v in series]


def chart(wide: pd.DataFrame, names: dict, unit: tuple, state: dict, kind: str = "line", decimals: int = 0) -> dict:
    """Tvar Chart.js, jaký čte pp-charts.js: labels + datasets, popisky i anglicky."""
    return {
        "labels": list(wide.index),
        "datasets": [{"label": names[key]["cs"], "label_en": names[key]["en"], "type": kind,
                      "data": _values(wide[key], decimals)} for key in wide.columns],
        "unit": unit[0], "unit_en": unit[1],
        "stav_k": state["stav_k"], "fetched_at": state.get("fetched_at"),
    }


# ── texty ────────────────────────────────────────────────────────────────────

def day_text(iso: str, lang: str) -> str:
    d = date.fromisoformat(iso)
    month = (MONTHS_CS if lang == "cs" else MONTHS_EN)[d.month - 1]
    return f"{d.day}.{NBSP}{month}{NBSP}{d.year}" if lang == "cs" else f"{d.day}{NBSP}{month}{NBSP}{d.year}"


def _last(wide: pd.DataFrame, key: str, offset: int = 0):
    """Hodnota řady k poslednímu dni (offset 7 = o týden dřív); None, když chybí."""
    if key not in wide.columns or len(wide) <= offset:
        return None
    value = wide[key].iloc[-1 - offset]
    return None if pd.isna(value) else int(value)


def weekly(wide: pd.DataFrame, last_day: str) -> pd.DataFrame:
    """
    Součty po ISO týdnech („2026-W47“) — jen úplné týdny: všech sedm dní v datech
    a neděle nejpozději v den „stavu k datu“. Běžící týden by vedle úplného vypadal
    jako propad.
    """
    if wide.empty:
        return wide
    days = pd.to_datetime(wide.index)
    iso = days.isocalendar()
    key = iso["year"].astype(str).values + "-W" + iso["week"].astype(int).map("{:02d}".format).values
    grouped = wide.groupby(key)
    sums, counts = grouped.sum(min_count=1), grouped.size()
    last = date.fromisoformat(last_day)
    done = [w for w in sums.index
            if counts[w] == 7 and date.fromisocalendar(int(w[:4]), int(w[6:]), 7) <= last]
    return sums.loc[done]


def week_text(week: str, lang: str) -> str:
    """„2026-W47“ → „16.–22. listopadu 2026“ / „16–22 November 2026“."""
    start = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    end = start + timedelta(days=6)
    months = MONTHS_CS if lang == "cs" else MONTHS_EN

    def d(x: date) -> str:
        return f"{x.day}.{NBSP}{months[x.month - 1]}" if lang == "cs" else f"{x.day}{NBSP}{months[x.month - 1]}"

    if start.month == end.month:
        text = f"{start.day}.–{d(end)}" if lang == "cs" else f"{start.day}–{d(end)}"
    else:
        text = f"{d(start)} – {d(end)}"
    return f"{text}{NBSP}{end.year}"


def load_holidays() -> dict:
    """Týdny se státním svátkem nebo školními prázdninami — tabulka sdílená s compute_resp.py."""
    try:
        import compute_resp
        return compute_resp.load_holidays()
    except Exception as e:                                        # tabulka je pomůcka, ne podmínka
        print(f"  [esurv] tabulku svátků nejde načíst ({e}) — sváteční týdny se neoznačí")
        return {}


def holiday_kind(week: str, table: dict) -> str | None:
    import compute_resp
    return compute_resp.holiday_kind(week, table) if table else None


HOLIDAY_TEXT = {
    "public": ("státní svátek", "a public holiday"),
    "school": ("školní prázdniny", "school holidays"),
    "both": ("státní svátek a školní prázdniny", "a public holiday and school holidays"),
}


def _change(f: Facts, name: str, now: int, before: int | None) -> bool:
    """Do faktů zapíše změnu proti předchozímu týdnu; False, když ji nejde spočítat."""
    if not before:
        return False
    pct = 100 * (now - before) / before
    if abs(pct) < 0.5:
        f.text(name, "stejně jako", "the same as")
    else:
        amount = fmt_num(abs(pct), "cs", 0), fmt_num(abs(pct), "en", 0)
        f.text(name, f"o {amount[0]}{NBSP}% {'více' if pct > 0 else 'méně'} než",
               f"{amount[1]}% {'more' if pct > 0 else 'fewer'} than")
    return True


def summary(data: dict, holidays: dict | None = None) -> dict:
    """
    Dlaždice, strojově čitelný souhrn po nemocech a tři bloky faktů s komentářem.

    Komentář srovnává CELÉ ISO TÝDNY, ne klouzavých sedm dní: klouzavé okno, ve
    kterém leží státní svátek, dá falešný skok (17. 11. 2026 se skoro nehlásilo,
    sedm dní do 24. 11. pak vyšlo „o 83 % víc“). Když je svátek nebo prázdniny
    v jednom ze srovnávaných týdnů, změna se nepíše a místo ní je upozornění.
    """
    holidays = load_holidays() if holidays is None else holidays
    state, last = data["state"], data["state"]["stav_k"]
    cases = national(data["denni"], "pripady")
    new_adm = national(data["denni"], "hosp_nove")
    current = national(data["denni"], "hosp_aktualne")
    hosp = data["nemocnice"].assign(jip_aro=lambda x: x["jip"].fillna(0) + x["aro"].fillna(0))
    icu, deaths = national(hosp, "jip_aro"), national(hosp, "umrti_hosp")
    cases_w, adm_w, icu_w, deaths_w = (weekly(x, last) for x in (cases, new_adm, icu, deaths))
    cases7, adm7 = rolling7(cases), rolling7(new_adm)

    out = {"posledni_datum": last, "slozka": state["slozka"], "zpracovano": state.get("zpracovano"),
           "fetched_at": state.get("fetched_at"), "nemoci": {}}
    week = cases_w.index[-1] if len(cases_w) else None
    prev = None
    if week:
        prev_start = date.fromisocalendar(int(week[:4]), int(week[6:]), 1) - timedelta(days=7)
        y, w, _ = prev_start.isocalendar()
        prev = f"{y}-W{w:02d}" if f"{y}-W{w:02d}" in cases_w.index else None
    out["tyden"], out["tyden_predchozi"] = week, prev

    def at(df, w, key):
        if w is None or w not in df.index or key not in df.columns or pd.isna(df.loc[w, key]):
            return None
        return int(df.loc[w, key])

    for key, names in DISEASES.items():
        out["nemoci"][key] = {
            "nazev": names["cs"], "nazev_en": names["en"],
            "pripady_tyden": at(cases_w, week, key), "pripady_tyden_predchozi": at(cases_w, prev, key),
            "hosp_nove_tyden": at(adm_w, week, key), "jip_aro_tyden": at(icu_w, week, key),
            "umrti_hosp_tyden": at(deaths_w, week, key),
            "hosp_aktualne": _last(current, key),
            # klouzavých 7 dní do posledního dne — pro stroje, text je nepoužívá
            "pripady_7d": _last(cases7, key), "hosp_nove_7d": _last(adm7, key),
        }
        if out["nemoci"][key]["hosp_aktualne"] is not None:
            out[f"hosp_aktualne_{key}"] = out["nemoci"][key]["hosp_aktualne"]      # dlaždice (stat-card)

    blocks, n = {}, out["nemoci"]
    kind_now = holiday_kind(week, holidays) if week else None
    kind_prev = holiday_kind(prev, holidays) if prev else None

    def holiday_caveat(f: Facts):
        """Upozornění na sváteční týden; vrací True, když srovnání týdnů nedává smysl."""
        hit = [(w, k) for w, k in ((week, kind_now), (prev, kind_prev)) if k]
        if not hit:
            return False
        w, k = hit[0]
        f.text("svatek_tyden", week_text(w, "cs"), week_text(w, "en"))
        f.text("svatek_druh", *HOLIDAY_TEXT[k])
        f.fact("caveat_holiday", "caveat",
               "V týdnu {svatek_tyden} byl {svatek_druh}; v takovém týdnu se hlásí méně a srovnání "
               "s jiným týdnem neukazuje skutečný vývoj.",
               "The week of {svatek_tyden} included {svatek_druh}; fewer cases are reported in such a "
               "week and a comparison with another week does not show the real trend.", required=True)
        f.say("V týdnu {svatek_tyden} byl {svatek_druh}, kdy se hlásí méně, proto týdny nesrovnáváme.",
              "The week of {svatek_tyden} included {svatek_druh}, when fewer cases are reported, so the weeks "
              "are not compared.", ["caveat_holiday"])
        return True

    # ── případy ──
    if week:
        period = {"cs": f"Týden {week_text(week, 'cs')}", "en": f"Week of {week_text(week, 'en')}"}
        f = Facts()
        f.text("tyden", week_text(week, "cs"), week_text(week, "en"))
        for key in DISEASES:
            f.count(f"n_{key}", n[key]["pripady_tyden"] or 0)
        f.fact("cases", "measurement",
               "Hlášené případy v týdnu {tyden}: chřipka {n_chripka}, covid-19 {n_covid19}, RSV {n_rsv}, "
               "ARI/ILI celkem {n_ari_ili}.",
               "Reported cases in the week of {tyden}: influenza {n_chripka}, COVID-19 {n_covid19}, RSV {n_rsv}, "
               "ARI/ILI in total {n_ari_ili}.", required=True)
        f.say("Lékaři v týdnu {tyden} nahlásili: chřipka {n_chripka}, covid-19 {n_covid19}, RSV {n_rsv}. "
              "Všech akutních respiračních infekcí a onemocnění podobných chřipce (ARI/ILI) bylo {n_ari_ili}.",
              "In the week of {tyden} physicians reported: influenza {n_chripka}, COVID-19 {n_covid19}, "
              "RSV {n_rsv}. Acute respiratory infections and influenza-like illness (ARI/ILI) together: "
              "{n_ari_ili}.", ["cases"])
        if not holiday_caveat(f) and _change(f, "zmena_chripka", n["chripka"]["pripady_tyden"] or 0,
                                             n["chripka"]["pripady_tyden_predchozi"]):
            f.count("p_chripka", n["chripka"]["pripady_tyden_predchozi"])
            f.text("tyden_pred", week_text(prev, "cs"), week_text(prev, "en"))
            f.fact("change_flu", "comparison",
                   "Chřipka v týdnu {tyden_pred}: {p_chripka} hlášených případů.",
                   "Influenza in the week of {tyden_pred}: {p_chripka} reported cases.")
            f.say("U chřipky je to {zmena_chripka} v předchozím týdnu ({p_chripka}).",
                  "For influenza that is {zmena_chripka} in the previous week ({p_chripka}).",
                  ["change_flu"], optional=True)
        f.fact("caveat_reports", "caveat",
               "Jde o hlášení lékařů; poslední týden se u zdroje ještě může doplnit.",
               "These are physicians' reports; the last week may still be completed at the source.")
        f.say("Poslední týden se u zdroje ještě může doplnit.",
              "The last week may still be completed at the source.", ["caveat_reports"], optional=True)
        blocks["cases"] = {"period": period, **f.block()}

    # ── nemocnice ──
    if week and all(n[k]["hosp_aktualne"] is not None for k in ("chripka", "covid19", "rsv")):
        f = Facts()
        f.text("den", day_text(last, "cs"), day_text(last, "en"))
        f.text("tyden", week_text(week, "cs"), week_text(week, "en"))
        for key in ("chripka", "covid19", "rsv"):
            f.count(f"h_{key}", n[key]["hosp_aktualne"])
            f.count(f"a_{key}", n[key]["hosp_nove_tyden"] or 0)
            f.count(f"i_{key}", n[key]["jip_aro_tyden"] or 0)
        f.fact("in_hospital", "measurement",
               "V nemocnici k {den}: chřipka {h_chripka}, covid-19 {h_covid19}, RSV {h_rsv}.",
               "In hospital on {den}: influenza {h_chripka}, COVID-19 {h_covid19}, RSV {h_rsv}.", required=True)
        f.fact("admissions", "measurement",
               "Nově přijatí v týdnu {tyden}: chřipka {a_chripka}, covid-19 {a_covid19}, RSV {a_rsv}.",
               "New admissions in the week of {tyden}: influenza {a_chripka}, COVID-19 {a_covid19}, RSV {a_rsv}.",
               required=True)
        f.fact("icu", "measurement",
               "Nově na JIP nebo ARO v týdnu {tyden}: chřipka {i_chripka}, covid-19 {i_covid19}, RSV {i_rsv}.",
               "New admissions to intensive care in the week of {tyden}: influenza {i_chripka}, "
               "COVID-19 {i_covid19}, RSV {i_rsv}.")
        f.say("V nemocnicích k {den} leželo: s chřipkou {h_chripka}, s covidem-19 {h_covid19}, s RSV {h_rsv}.",
              "In hospital on {den}: with influenza {h_chripka}, with COVID-19 {h_covid19}, with RSV {h_rsv}.",
              ["in_hospital"])
        f.say("Nově přijatých v týdnu {tyden}: chřipka {a_chripka}, covid-19 {a_covid19}, RSV {a_rsv}.",
              "New admissions in the week of {tyden}: influenza {a_chripka}, COVID-19 {a_covid19}, RSV {a_rsv}.",
              ["admissions"])
        f.say("Z toho na JIP nebo ARO: chřipka {i_chripka}, covid-19 {i_covid19}, RSV {i_rsv}.",
              "Of these to intensive care: influenza {i_chripka}, COVID-19 {i_covid19}, RSV {i_rsv}.",
              ["icu"], optional=True)
        blocks["hospital"] = {"period": {"cs": f"Stav k {day_text(last, 'cs')}, příjmy v týdnu {week_text(week, 'cs')}",
                                         "en": f"State on {day_text(last, 'en')}, admissions in the week of "
                                               f"{week_text(week, 'en')}"}, **f.block()}

    # ── očkování ──
    doses = vaccination_series(data["ockovani"])
    if not doses.empty:
        f = Facts()
        f.text("den", day_text(doses.index[-1], "cs"), day_text(doses.index[-1], "en"))
        recent = data["ockovani"]
        recent = recent[(recent["rozmer"] == "vek") & (recent["kategorie"] == TOTAL)
                        & (recent["datum"] > (date.fromisoformat(doses.index[-1]) - timedelta(days=7)).isoformat())]
        out["ockovani"] = {}
        for key in VACCINES:
            total = int(doses[key].iloc[-1]) if key in doses.columns and pd.notna(doses[key].iloc[-1]) else 0
            last7 = int(recent[recent["nemoc"] == key]["davky"].sum())
            out["ockovani"][key] = {"davky_celkem": total, "davky_7d": last7}
            f.count(f"v_{key}", total)
            f.count(f"w_{key}", last7)
        f.text("w_davek", plural_cs(out["ockovani"]["chripka"]["davky_7d"], "dávka", "dávky", "dávek"), "doses")
        f.fact("doses", "measurement",
               "Podané dávky od začátku sezóny do {den}: proti chřipce {v_chripka}, proti covidu-19 {v_covid19}, "
               "proti RSV {v_rsv}.",
               "Doses given from the start of the season to {den}: against influenza {v_chripka}, against "
               "COVID-19 {v_covid19}, against RSV {v_rsv}.", required=True)
        f.fact("doses_week", "measurement",
               "Dávky za posledních 7 dní: chřipka {w_chripka}, covid-19 {w_covid19}, RSV {w_rsv}.",
               "Doses in the last 7 days: influenza {w_chripka}, COVID-19 {w_covid19}, RSV {w_rsv}.")
        f.say("Od začátku sezóny do {den} bylo podáno: proti chřipce {v_chripka}, proti covidu-19 {v_covid19}, "
              "proti RSV {v_rsv} dávek.",
              "From the start of the season to {den}, doses given: against influenza {v_chripka}, against "
              "COVID-19 {v_covid19}, against RSV {v_rsv}.", ["doses"])
        f.say("Za posledních sedm dní přibylo proti chřipce {w_chripka} {w_davek}.",
              "In the last seven days {w_chripka} {w_davek} against influenza were added.",
              ["doses_week"], optional=True)
        blocks["vaccination"] = {"period": {"cs": f"Sezóna do {day_text(doses.index[-1], 'cs')}",
                                            "en": f"Season to {day_text(doses.index[-1], 'en')}"}, **f.block()}
    out["blocks"] = blocks
    return out


# ── grafy podle věku ─────────────────────────────────────────────────────────

def _age_bounds(key: str) -> tuple[int, int | None]:
    if key.endswith("+"):
        return int(key[:-1]), None
    lo, _, hi = key.partition("-")
    return int(lo), int(hi or lo)


def by_age(data: dict, column: str, days: int, pop: pd.DataFrame | None, unit: tuple) -> dict | None:
    """Součet za posledních `days` dní na 100 tisíc obyvatel dané věkové skupiny, celá ČR."""
    if pop is None:
        return None
    df, last = data["denni"], data["state"]["stav_k"]
    start = (date.fromisoformat(last) - timedelta(days=days - 1)).isoformat()
    sub = df[(df["kraj_kod"] == "CZ") & (df["vek"] != TOTAL) & (df["datum"] >= start) & (df["datum"] <= last)]
    counts = sub.groupby(["nemoc", "vek"])[column].sum().unstack("vek").reindex(index=list(DISEASES), columns=AGE_KEYS)
    year = date.fromisoformat(last).year
    people = {a: population(pop, year, *_age_bounds(a)) for a in AGE_KEYS}
    labels = [age_label(*_age_bounds(a)) for a in AGE_KEYS]
    return {
        "labels": [x["cs"] for x in labels], "labels_en": [x["en"] for x in labels],
        "datasets": [{"label": DISEASES[k]["cs"], "label_en": DISEASES[k]["en"], "type": "bar",
                      "data": [round(100000 * float(counts.loc[k, a] or 0) / people[a][0], 1) if people[a][0] else None
                               for a in AGE_KEYS],
                      "pocty": [int(counts.loc[k, a] or 0) for a in AGE_KEYS]} for k in DISEASES],
        "unit": unit[0], "unit_en": unit[1], "obdobi": f"{start} – {last}",
        "population_year": people[AGE_KEYS[0]][1], "stav_k": last, "fetched_at": data["state"].get("fetched_at"),
    }


# ── kraje ────────────────────────────────────────────────────────────────────

def region_map(data: dict, disease: str) -> dict | None:
    """Nové případy za 7 dní na 100 tisíc po krajích — hodnoty spočítané zdrojem."""
    u = data["uzemi"]
    rows = u[(u["nemoc"] == disease) & (u["uroven"] == "kraj") & u["nuts"].isin(NUTS3)]
    if len(rows) != len(NUTS3):
        print(f"  [esurv_map_{disease}] ve zdroji není všech 14 krajů — mapu nezapisuji")
        return None
    rows = rows.set_index("nuts")
    return {
        # „year“ čte mapa portálu jako popisek období v bublině.
        "year": rows["k_datu"].iloc[0], "stav_k": rows["k_datu"].iloc[0],
        "unit": "nových případů za 7 dní na 100 000 obyvatel", "unit_en": "new cases in 7 days per 100,000 population",
        "regions": {code: f"{float(rows.loc[code, 'pripady_7d_100k']):.1f}" for code in NUTS3},
        "hospitalizovani": {code: int(rows.loc[code, "hosp_aktualne"]) for code in NUTS3
                            if pd.notna(rows.loc[code, "hosp_aktualne"])},
        "labels": NUTS3_NAMES, "labels_en": {code: labels_en.region(code) or NUTS3_NAMES[code] for code in NUTS3},
        "fetched_at": data["state"].get("fetched_at"),
    }


# ── očkování a laboratoře ────────────────────────────────────────────────────

def vaccination_series(vax: pd.DataFrame) -> pd.DataFrame:
    """Kumulativní počet dávek od začátku sezóny, den × očkování, celá ČR."""
    sub = vax[(vax["rozmer"] == "vek") & (vax["kategorie"] == TOTAL)]
    if sub.empty:
        return pd.DataFrame()
    wide = sub.pivot(index="datum", columns="nemoc", values="davky")
    days = pd.date_range(wide.index.min(), wide.index.max(), freq="D").strftime("%Y-%m-%d")
    return wide.reindex(days).fillna(0).cumsum().reindex(columns=list(VACCINES))


def vaccination_by_age(data: dict, pop: pd.DataFrame | None) -> dict | None:
    """Dávky na 100 obyvatel podle věku. Jen chřipka a covid — RSV má jiné věkové skupiny."""
    if pop is None:
        return None
    vax, last = data["ockovani"], data["state"]["stav_k"]
    sub = vax[(vax["rozmer"] == "vek") & vax["kategorie"].isin(AGE_KEYS) & vax["nemoc"].isin(["chripka", "covid19"])]
    counts = sub.groupby(["nemoc", "kategorie"])["davky"].sum().unstack("kategorie").reindex(columns=AGE_KEYS)
    year = date.fromisoformat(last).year
    people = {a: population(pop, year, *_age_bounds(a)) for a in AGE_KEYS}
    labels = [age_label(*_age_bounds(a)) for a in AGE_KEYS]
    keys = [k for k in ("chripka", "covid19") if k in counts.index]
    if not keys:
        return None
    return {
        "labels": [x["cs"] for x in labels], "labels_en": [x["en"] for x in labels],
        "datasets": [{"label": VACCINES[k]["cs"], "label_en": VACCINES[k]["en"], "type": "bar",
                      "data": [round(100 * float(counts.loc[k, a] or 0) / people[a][0], 1) if people[a][0] else None
                               for a in AGE_KEYS],
                      "pocty": [int(counts.loc[k, a] or 0) for a in AGE_KEYS]} for k in keys],
        "unit": "dávek na 100 obyvatel věkové skupiny", "unit_en": "doses per 100 inhabitants of the age group",
        "obdobi": f"{sub['datum'].min()} – {sub['datum'].max()}",
        "population_year": people[AGE_KEYS[0]][1], "stav_k": last, "fetched_at": data["state"].get("fetched_at"),
    }


def lab(data: dict) -> dict:
    """Laboratorně potvrzené infekce, 7denní součet podle dne odběru."""
    lb = data["laboratore"]
    wide = lb.pivot(index="datum", columns="nemoc", values="pozitivni")
    days = pd.date_range(wide.index.min(), wide.index.max(), freq="D").strftime("%Y-%m-%d")
    wide = rolling7(wide.reindex(days).reindex(columns=list(DISEASES))).dropna(how="all")
    obj = chart(wide, DISEASES, ("laboratorně potvrzených za 7 dní", "laboratory-confirmed in 7 days"), data["state"])
    newest = lb[lb["datum"] == lb["datum"].max()].iloc[0]
    obj["laboratore"] = {"celkem": int(newest["laboratori_celkem"]), "zapojene_do_ews": int(newest["laboratori_ews"])}
    return obj


# ── vše dohromady ────────────────────────────────────────────────────────────

def build(data: dict, pop: pd.DataFrame | None) -> dict:
    """{jméno grafu: obsah}. Graf, pro který chybí vstup, ve výsledku není."""
    state = data["state"]
    hosp = data["nemocnice"].assign(jip_aro=lambda x: x["jip"].fillna(0) + x["aro"].fillna(0))
    out = {
        "esurv_summary": summary(data),
        "esurv_cases": chart(rolling7(national(data["denni"], "pripady")).dropna(how="all"), DISEASES,
                             ("hlášených případů za 7 dní", "reported cases in 7 days"), state),
        "esurv_hosp_current": chart(trim(national(data["denni"], "hosp_aktualne")), DISEASES,
                                    ("hospitalizovaných v daný den", "patients in hospital on the day"), state),
        "esurv_hosp_new": chart(trim(rolling7(national(data["denni"], "hosp_nove"))), DISEASES,
                                ("nově přijatých za 7 dní", "new admissions in 7 days"), state),
        "esurv_hosp_icu": chart(trim(rolling7(national(hosp, "jip_aro"))), DISEASES,
                                ("nově přijatých na JIP nebo ARO za 7 dní", "new intensive-care admissions in 7 days"),
                                state),
        "esurv_hosp_deaths": chart(trim(rolling7(national(hosp, "umrti_hosp"))), DISEASES,
                                   ("zemřelých za hospitalizace za 7 dní", "deaths during hospital stay in 7 days"),
                                   state),
        "esurv_lab": lab(data),
    }
    doses = vaccination_series(data["ockovani"])
    if not doses.empty:
        out["esurv_vaccination"] = chart(doses, VACCINES, ("podaných dávek od začátku sezóny",
                                                           "doses given since the start of the season"), state)
        vaccines = (data["state"].get("vakciny") or {})
        out["esurv_vaccination"]["vakciny"] = {k: v for k, v in vaccines.items() if v}
    optional = {
        "esurv_cases_age": by_age(data, "pripady", 7, pop, ("hlášených případů za 7 dní na 100 000 obyvatel věkové skupiny",
                                                            "reported cases in 7 days per 100,000 of the age group")),
        "esurv_hosp_age": by_age(data, "hosp_nove", HOSP_AGE_DAYS, pop,
                                 (f"nově přijatých za {HOSP_AGE_DAYS} dní na 100 000 obyvatel věkové skupiny",
                                  f"new admissions in {HOSP_AGE_DAYS} days per 100,000 of the age group")),
        "esurv_vaccination_age": vaccination_by_age(data, pop),
        **{f"esurv_map_{d}": region_map(data, d) for d in DISEASES},
    }
    out.update({name: obj for name, obj in optional.items() if obj})
    return out


def save(name: str, obj: dict, force: bool = False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    # I nepřepsaný soubor je ověřený: nad dnešním stažením vyšel stejně (last_checked.py).
    last_checked.mark_chart(name)
    if not force and unchanged(path, obj):
        print(f"  [{name}] beze změny — nechávám, jak je (--force přepíše)")
        return
    path.write_text(json.dumps(chart_meta.attach(name, obj), ensure_ascii=False), encoding="utf-8")
    print(f"  [{name}] → {path}")


def main() -> int:
    data = load()
    if data is None:
        print("  [esurv] denní e-surveillance ÚZIS není stažená — přeskakuji")
        return 0
    pop = pd.read_csv(POP_AGE_FILE) if POP_AGE_FILE.exists() else None
    if pop is None:
        print("  [esurv] chybí populace podle věku (csu/population_age.csv) — grafy podle věku vynechávám")
    force = "--force" in sys.argv[1:]
    for name, obj in build(data, pop).items():
        save(name, obj, force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

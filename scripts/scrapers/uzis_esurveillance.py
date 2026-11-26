"""
ÚZIS — respirační infekce, denní reporting e-surveillance (datový souhrn NZIP 2860).

Zdroj:  „Respirační infekce – denní reporting e-surveillance“,
        https://www.nzip.cz/data/2860-respiracni-infekce-denni-reporting-esurveillance-datovy-souhrn
        Soubory leží na sdíleném úložišti ÚZIS (Nextcloud), každý den nová složka
        `RRRR-MM-DD/` s 56 soubory (asi 21 MB). Stav dat je k předchozímu dni.
Licence: stránka souhrnu ani soubory licenci neuvádějí, jen citaci — viz sources.yaml.

Čtyři onemocnění: chřipka, COVID-19, RSV a souhrnně ARI/ILI. Bere se z toho:

  EW05  denní řada od 26. 5. 2025, ČR a 14 krajů: nově hlášené případy, nově a aktuálně
        hospitalizovaní, nově přijatí přímo na JIP, zemřelí za hospitalizace; první čtyři
        i podle 12 věkových skupin                           → esurv_denni.csv
  eHos03 denní řada hospitalizací, ČR a kraje: typ péče u nově přijatých (JIP, ARO,
        kyslík, HFNO, UPV/ECMO) a úmrtí                      → esurv_nemocnice.csv
  eVak01, eVak02  podané dávky očkování po dnech podle kraje a podle věku (chřipka,
        COVID-19, RSV)                                       → esurv_ockovani.csv
  EW07  laboratorně potvrzené infekce po dnech a počet hlásících laboratoří
                                                             → esurv_laboratore.csv
  EW03  kraje a okresy k poslednímu dni: 7denní a 14denní incidence na 100 tisíc,
        hospitalizovaní a úmrtí na 100 tisíc (hodnoty spočítané zdrojem)
                                                             → esurv_uzemi.csv
  EW04  nově hlášení, hospitalizovaní a přijatí na JIP podle toho, zda byli v sezóně
        očkovaní (chřipka, COVID-19)                         → esurv_ockovani_stav.csv
  stav  složka, „stav k datu“, čas zpracování u zdroje, čas stažení → esurv_stav.json

Nebere se: přehled po jednotlivých nemocnicích (eHos02) a po obcích (eVak03) — portál
neukazuje nic pod úrovní okresu; EW02 a EW06 jsou přepočty řady EW05; textové souhrny
(DOCX, PDF) jsou tatáž čísla ve větách.

Co ta čísla jsou (z hlaviček souborů): „nově nakažení“ jsou hlášení lékařů
(automatizovaná hlášení vybraných infekčních nemocí do ISIN, tedy EWS), hospitalizace
hlásí zapojené nemocnice a počítá se každá hospitalizace, v jejímž průběhu byla infekce
zaznamenána. „V posledních dnech může docházet ke zpětnému doplňování a korekcím
hodnot.“ Není to sentinelová nemocnost ILI/ARI a s prahy MEM se nesrovnává.

Tvar zdroje (ověřeno na složce 2026-11-25):
  - Každý soubor nese celou historii, starší složky tedy nejsou potřeba. Stahuje se
    jen nejnovější složka a jen 24 souborů z ní (asi 11 MB).
  - Data jsou text „DD.MM.RRRR“, ne datum Excelu.
  - EW05 má na listu vedle sebe pět bloků sloupců; každý začíná sloupcem „Datum“
    a mezi bloky jsou místy prázdné sloupce. Poloha se proto čte z hlavičky, ne
    z pevných čísel sloupců. Listy krajů se jmenují zkratkou (PHA, STC, … JMK).
  - eHos03 má list na kraj se jménem „PHA CZ010“; kód kraje je i ve sloupci „Kraj“
    (celá ČR = CZ000).
  - Složka pro daný den vzniká ráno, ale ne vždy: 26. 11. 2026 večer ještě nebyla.
  - EW07 má některé dny zapsané dvakrát se stejnými čísly (6 z 91 dnů ve složce
    2026-11-25). Úplně shodné řádky se slučují; stejný den s různými čísly je chyba.
  - Kraje v EW05 a EW03 jsou podle bydliště pacienta, v eHos03 se od nich u krajů
    liší (součet za ČR je v obou stejný) — nejspíš jde o kraj nemocnice. Mapy
    a věk se proto berou z EW05/EW03, z eHos03 jen typ péče za celou ČR.
  - EW03 má na listu krajů i řádek „CZ099“ bez jména (neznámé bydliště).

Výpis sdílené složky jde přes veřejné rozhraní WebDAV jen s hlavičkou
`X-Requested-With: XMLHttpRequest` (bez ní server vrací 401). Odkaz „/download“
vrací ZIP celého úložiště (2,7 GB v 11/2026) a nepoužívá se.
"""

import io
import json
import re
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import openpyxl
import pandas as pd
import requests

SHARE_TOKEN = "k6Y8Y3KpMRBcczZ"
DAV = f"https://share.uzis.cz/public.php/dav/files/{SHARE_TOKEN}"
LANDING = "https://www.nzip.cz/data/2860-respiracni-infekce-denni-reporting-esurveillance-datovy-souhrn"
UA = "pathogen-portal-cz/1.0 (+https://pathogens.vm.cesnet.cz/)"
HEADERS = {"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}
TIMEOUT = 120
PAUSE = 0.5                     # mezi soubory; úložiště je sdílené, ne datový server

STATE_NAME = "esurv_stav.json"
CSV = {
    "denni": "esurv_denni.csv",
    "nemocnice": "esurv_nemocnice.csv",
    "ockovani": "esurv_ockovani.csv",
    "laboratore": "esurv_laboratore.csv",
    "uzemi": "esurv_uzemi.csv",
    "ockovani_stav": "esurv_ockovani_stav.csv",
}

# Zdroj je denní, ale složka občas den dva nevznikne (víkend, svátek, výpadek).
# Týden bez nové složky už znamená, že se zdroj přestal obnovovat nebo přestěhoval.
MAX_AGE_DAYS = 7

# Jméno nemoci v názvu souboru → klíč v našich CSV.
DISEASES = {"Chripka": "chripka", "COVID-19": "covid19", "RSV": "rsv", "ARIILI": "ari_ili"}
VACCINES = ("Chripka", "COVID-19", "RSV")           # ARI/ILI očkování nemá
VAX_STATUS = ("Chripka", "COVID-19")                # EW04 vychází jen pro tyto dvě

SHEET_TO_NUTS3 = {
    "ČR": "CZ", "PHA": "CZ010", "STC": "CZ020", "JHC": "CZ031", "PLK": "CZ032", "KVK": "CZ041",
    "ULK": "CZ042", "LBK": "CZ051", "HKK": "CZ052", "PAK": "CZ053", "VYS": "CZ063", "JMK": "CZ064",
    "OLK": "CZ071", "ZLK": "CZ072", "MSK": "CZ080",
}
REGION_TO_NUTS3 = {
    "Hlavní město Praha": "CZ010", "Středočeský kraj": "CZ020", "Jihočeský kraj": "CZ031",
    "Plzeňský kraj": "CZ032", "Karlovarský kraj": "CZ041", "Ústecký kraj": "CZ042",
    "Liberecký kraj": "CZ051", "Královéhradecký kraj": "CZ052", "Pardubický kraj": "CZ053",
    "Kraj Vysočina": "CZ063", "Jihomoravský kraj": "CZ064", "Olomoucký kraj": "CZ071",
    "Zlínský kraj": "CZ072", "Moravskoslezský kraj": "CZ080", "ČR": "CZ", "Česká republika": "CZ",
    "Neznámé bydliště": "neznamy",
}

# Dvanáct věkových skupin denní řady (EW05) a očkování proti chřipce a covidu:
# klíč „od-do“ s horní mezí včetně, 75+ bez meze. Očkování proti RSV má jiné
# skupiny (měsíce u kojenců, 65+), ty se čtou z hlavičky přes age_key().
AGE_KEYS = ["0", "1-4", "5-9", "10-14", "15-19", "20-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"]
TOTAL = "celkem"


def age_key(label: str) -> str | None:
    """
    Věková skupina tak, jak ji zdroj píše → klíč. None, když text věk není.

    „0 let“ → 0, „1 až 4 roky“ → 1-4, „75 a více let“ i „65 let a více“ → 75+ / 65+,
    „3 měsíce“ → 3m, „1 rok“ → 1, „Celkem“ → celkem.
    """
    t = _text(label).lower()
    if t == "celkem":
        return TOTAL
    m = re.fullmatch(r"(\d+) až (\d+) (?:let|roky|rok|roků)", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d+) (?:a více let|let a více)", t)
    if m:
        return f"{m.group(1)}+"
    m = re.fullmatch(r"(\d+) (?:měsíc|měsíce|měsíců)", t)
    if m:
        return f"{m.group(1)}m"
    m = re.fullmatch(r"(\d+) (?:let|rok|roky|roků)", t)
    return m.group(1) if m else None

# EW05: nadpis bloku (řádek nad hlavičkou) → sloupec v esurv_denni.csv.
EW05_AGE_BLOCKS = {
    "Nově nakažení dle věkových kategorií": "pripady",
    "Nově hospitalizovaní dle věkových kategorií": "hosp_nove",
    "Aktuálně hospitalizovaní celkem dle věkových kategorií": "hosp_aktualne",
    "Hospitalizovaní na JIP nově dle věkových kategorií": "jip_nove",
}
EW05_SUMMARY = {
    "Počet nově nakažených": "pripady",
    "Počet nově hospitalizovaných": "hosp_nove",
    "Počet aktuálně hospitalizovaných v daném dni": "hosp_aktualne",
    "Počet nově přijatých přímo na JIP": "jip_nove",
    "Počet zemřelých za hospitalizace": "umrti_hosp",
}
DENNI_COLUMNS = ["datum", "nemoc", "kraj_kod", "vek", "pripady", "hosp_nove", "hosp_aktualne",
                 "jip_nove", "umrti_hosp"]

EHOS03 = {
    "Nově hospitalizovaní": "hosp_nove", "Aktuálně hospitalizovaní": "hosp_aktualne", "JIP": "jip",
    "ARO": "aro", "Kyslík": "kyslik", "HFNO": "hfno", "UPV/ECMO": "upv_ecmo",
    "Úmrtí během hospitalizace": "umrti_hosp",
}
EW07 = {
    "Počet laboratoří celkem": "laboratori_celkem",
    "Počet laboratoří, které jsou zapojeny do EWS": "laboratori_ews",
    "Počet laboratoří, které daný den nahlásily test": "laboratori_hlasicich",
    "Negativní testy": "negativni", "Pozitivní testy": "pozitivni", "Testy celkem": "testy",
}
EW03 = {
    "Nové případy za 14 dní na 100 000 obyvatel": "pripady_14d_100k",
    "Nové případy za 7 dní na 100 000 obyvatel": "pripady_7d_100k",
    "Denní incidence na 100 tis.": "incidence_denni_100k",
    "Denní incidence 65+ na 100 tis. 65+": "incidence_denni_65_100k",
    "Souhrnná týdenní změna": "tydenni_zmena",
    "Hospitalizovaní celkem k danému dni": "hosp_aktualne",
    "Hospitalizovaní celkem / 100 tis. obyv. k danému dni": "hosp_aktualne_100k",
    "Nově hospitalizovaní za 7 dní": "hosp_nove_7d",
    "Nově hospitalizovaní za 7 dní /100 tis. obyv.": "hosp_nove_7d_100k",
    "Nově přijatí přímo na JIP/ARO za 7 dní": "jip_nove_7d",
    "Nově přijatí přímo na JIP/ARO za 7 dní /100 tis. obyv.": "jip_nove_7d_100k",
    "Počet úmrtí za hospitalizace za 7 dní": "umrti_hosp_7d",
    "Počet úmrtí za hospitalizace za 7 dní na 100 000 obyv.": "umrti_hosp_7d_100k",
}
EW04 = {
    "Počet nových nahlášených případů": "pripady",
    "Nakažení: neočkovaní v dané sezóně": "pripady_neockovani",
    "Nakažení: očkovaní v dané sezóně": "pripady_ockovani",
    "Počet nově hospitalizovaných": "hosp_nove",
    "Nově hospitalizovaní: neočkovaní v dané sezóně": "hosp_nove_neockovani",
    "Nově hospitalizovaní: očkovaní v dané sezóně": "hosp_nove_ockovani",
    "Počet nově přijatých přímo na JIP": "jip_nove",
    "Nově přijatí přímo na JIP: neočkovaní v dané sezóně": "jip_nove_neockovani",
    "Nově přijatí přímo na JIP: očkovaní v dané sezóně": "jip_nove_ockovani",
}

# Které soubory se z denní složky berou: (druh, podsložka reportu, které nemoci).
WANTED = [
    ("EW05", "REPORT-01 Early Warning", tuple(DISEASES)),
    ("EW03", "REPORT-01 Early Warning", tuple(DISEASES)),
    ("EW07", "REPORT-01 Early Warning", tuple(DISEASES)),
    ("EW04", "REPORT-01 Early Warning", VAX_STATUS),
    ("eHos03", "REPORT-02 Hospitalizace", tuple(DISEASES)),
    ("eVak01", "REPORT-03 Vakcinace", VACCINES),
    ("eVak02", "REPORT-03 Vakcinace", VACCINES),
]


# ── čtení buněk ──────────────────────────────────────────────────────────────

def _text(value) -> str:
    """Text buňky bez okrajových mezer a s jednou mezerou místo zalomení."""
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def _date(value) -> date | None:
    """„24.11.2026“ → datum. Cokoli jiného (řádek „Celkem“, prázdná buňka) → None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", _text(value))
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _num(value):
    """Číslo, nebo None u prázdné buňky. Prázdná buňka není nula — zdroj nulu píše."""
    if value is None or _text(value) == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"v datovém sloupci je logická hodnota {value!r}")
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    return float(_text(value).replace(",", "."))


def _rows(content: bytes, sheet=None) -> dict:
    """{jméno listu: [řádky]} — všechny listy kromě úvodního, nebo jen vyjmenované."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        names = [n for n in wb.sheetnames if n != "Úvod"] if sheet is None else [sheet]
        missing = [n for n in names if n not in wb.sheetnames]
        if missing:
            raise ValueError(f"chybí list {missing} (listy: {wb.sheetnames})")
        return {n: [list(r) for r in wb[n].iter_rows(values_only=True)] for n in names}
    finally:
        wb.close()


def _header_row(rows: list, first: str) -> int:
    """Index řádku hlavičky: první buňka začíná na `first`."""
    for i, row in enumerate(rows[:40]):
        if row and _text(row[0]).startswith(first):
            return i
    raise ValueError(f"nenašel jsem hlavičku začínající „{first}“ — změnil se tvar souboru")


def _columns(header: list, wanted: dict, what: str) -> dict:
    """{náš sloupec: index} podle textu hlavičky; chybějící sloupec je chyba."""
    found = {wanted[_text(c)]: i for i, c in enumerate(header) if _text(c) in wanted}
    missing = sorted(set(wanted.values()) - set(found))
    if missing:
        raise ValueError(f"{what}: v hlavičce chybí sloupce {missing} — změnil se tvar souboru")
    return found


def source_state(rows: list) -> dict:
    """„Zpracováno dne: 25.11.2026 06:00“ a „Stav k datu: 24.11.2026“ z prvních řádků listu."""
    out = {}
    for row in rows[:12]:
        text = _text(row[0]) if row else ""
        m = re.search(r"Zpracováno dne:\s*(\d{1,2}\.\d{1,2}\.\d{4})(?:\s+(\d{1,2}:\d{2}))?", text)
        if m:
            out["zpracovano"] = _date(m.group(1)).isoformat() + (f"T{m.group(2)}" if m.group(2) else "")
        m = re.search(r"Stav k datu:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
        if m:
            out["stav_k"] = _date(m.group(1)).isoformat()
    return out


# ── jednotlivé druhy souborů ─────────────────────────────────────────────────

def parse_ew05(content: bytes, disease: str) -> tuple[pd.DataFrame, dict]:
    """Denní řada případů a hospitalizací, ČR + kraje, celkem i podle věku."""
    sheets = _rows(content)
    unknown = set(sheets) - set(SHEET_TO_NUTS3)
    if unknown or len(sheets) != len(SHEET_TO_NUTS3):
        raise ValueError(f"EW05 {disease}: čekal jsem listy ČR a 14 krajů, jsou tu {sorted(sheets)}")
    out, state = [], {}
    for sheet, rows in sheets.items():
        region = SHEET_TO_NUTS3[sheet]
        state = state or source_state(rows)
        h = _header_row(rows, "Datum")
        header, titles = rows[h], rows[h - 1]
        summary = _columns(header, EW05_SUMMARY, f"EW05 {disease} [{sheet}] souhrn")
        # Věkové bloky: nadpis bloku leží nad jeho prvním věkovým sloupcem, sloupec
        # „Datum“ bloku je o jeden až dva vlevo. Věkové sloupce jdou za sebou.
        blocks = {}
        for i, title in enumerate(titles):
            name = EW05_AGE_BLOCKS.get(_text(title))
            if not name:
                continue
            ages = {}
            for j in range(i, len(header)):
                key = age_key(header[j])
                if key and key != TOTAL:
                    ages[key] = j
                elif ages:
                    break
            if set(ages) != set(AGE_KEYS):
                raise ValueError(f"EW05 {disease} [{sheet}]: blok „{_text(title)}“ nemá všech 12 věkových skupin")
            blocks[name] = ages
        if set(blocks) != set(EW05_AGE_BLOCKS.values()):
            raise ValueError(f"EW05 {disease} [{sheet}]: chybí věkové bloky "
                             f"{sorted(set(EW05_AGE_BLOCKS.values()) - set(blocks))}")
        for row in rows[h + 1:]:
            day = _date(row[0]) if row else None
            if day is None:
                continue
            rec = {"datum": day.isoformat(), "nemoc": disease, "kraj_kod": region, "vek": TOTAL}
            rec.update({col: _num(row[i]) for col, i in summary.items()})
            out.append(rec)
            for age in AGE_KEYS:
                rec = {"datum": day.isoformat(), "nemoc": disease, "kraj_kod": region, "vek": age}
                rec.update({col: _num(row[idx[age]]) if idx[age] < len(row) else None
                            for col, idx in blocks.items()})
                out.append(rec)
    return pd.DataFrame(out, columns=DENNI_COLUMNS), state


def parse_ehos03(content: bytes, disease: str) -> pd.DataFrame:
    """Denní hospitalizace podle typu péče, ČR + kraje."""
    out = []
    for sheet, rows in _rows(content).items():
        h = _header_row(rows, "Datum")
        header = rows[h]
        cols = _columns(header, EHOS03, f"eHos03 {disease} [{sheet}]")
        kraj = next(i for i, c in enumerate(header) if _text(c) == "Kraj")
        for row in rows[h + 1:]:
            day = _date(row[0]) if row else None
            if day is None:
                continue
            code = _text(row[kraj])
            if not re.fullmatch(r"CZ0\d\d", code):
                raise ValueError(f"eHos03 {disease} [{sheet}]: neznámý kód kraje {code!r}")
            rec = {"datum": day.isoformat(), "nemoc": disease, "kraj_kod": "CZ" if code == "CZ000" else code}
            rec.update({col: _num(row[i]) for col, i in cols.items()})
            out.append(rec)
    return pd.DataFrame(out, columns=["datum", "nemoc", "kraj_kod", *EHOS03.values()])


def _parse_doses(rows: list, categorize, required: set, disease: str, dimension: str, what: str) -> tuple[list, str]:
    """
    Tabulka „Datum × kategorie“ s dávkami; řádek „Celkem“ na konci se nečte.

    `categorize(text hlavičky)` vrací klíč kategorie, nebo None pro sloupec, který
    kategorií není. `required` jsou kategorie, bez kterých tabulka nedává smysl.
    """
    h = _header_row(rows, "Datum")
    header = rows[h]
    cols = {}
    for i, c in enumerate(header[1:], start=1):
        if not _text(c):
            continue
        key = categorize(_text(c))
        if key is None:
            raise ValueError(f"{what} {disease}: neznámý sloupec {_text(c)!r} — změnil se tvar souboru")
        cols[key] = i
    missing = sorted(required - set(cols))
    if missing:
        raise ValueError(f"{what} {disease}: v hlavičce chybí {missing} — změnil se tvar souboru")
    out = []
    for row in rows[h + 1:]:
        day = _date(row[0]) if row else None
        if day is None:
            continue
        for cat, i in cols.items():
            value = _num(row[i]) if i < len(row) else None
            if value is not None:
                out.append({"datum": day.isoformat(), "nemoc": disease, "rozmer": dimension,
                            "kategorie": cat, "davky": int(value)})
    # „Látky využité pro vakcinaci/imunizaci v dané sezóně - …“ a období sezóny
    note = " ".join(_text(r[0]) or _text(r[1] if len(r) > 1 else None) for r in rows[:h]
                    if r and re.search(r"Látky využité|CELKOVÝ POČET", _text(r[0]) + _text(r[1] if len(r) > 1 else "")))
    return out, note


def parse_evak(content_regions: bytes, content_ages: bytes, disease: str) -> tuple[pd.DataFrame, str]:
    """Podané dávky po dnech: podle kraje bydliště (eVak01) a podle věku (eVak02)."""
    by_region, note = _parse_doses(_rows(content_regions, "Kraje")["Kraje"], REGION_TO_NUTS3.get,
                                   set(SHEET_TO_NUTS3.values()), disease, "kraj", "eVak01")
    # Věkové skupiny se liší podle očkování (RSV: měsíce u kojenců a 65+), proto
    # se berou z hlavičky; povinný je jen součet.
    by_age, _ = _parse_doses(_rows(content_ages, "Věky")["Věky"], age_key, {TOTAL}, disease, "vek", "eVak02")
    return pd.DataFrame(by_region + by_age, columns=["datum", "nemoc", "rozmer", "kategorie", "davky"]), note


def parse_ew07(content: bytes, disease: str) -> pd.DataFrame:
    """Laboratorně potvrzené infekce po dnech odběru a počty hlásících laboratoří."""
    rows = _rows(content, "Data")["Data"]
    h = _header_row(rows, "Datum odběru vzorku")
    cols = _columns(rows[h], EW07, f"EW07 {disease}")
    out = []
    for row in rows[h + 1:]:
        day = _date(row[0]) if row else None
        if day is None:
            continue
        rec = {"datum": day.isoformat(), "nemoc": disease}
        rec.update({col: _num(row[i]) for col, i in cols.items()})
        out.append(rec)
    return pd.DataFrame(out, columns=["datum", "nemoc", *EW07.values()])


def parse_ew03(content: bytes, disease: str) -> pd.DataFrame:
    """Kraje a okresy k poslednímu dni — hodnoty na 100 tisíc tak, jak je spočítal zdroj."""
    sheets = _rows(content)
    out = []
    for sheet, level in (("Kraj", "kraj"), ("Okres", "okres")):
        if sheet not in sheets:
            raise ValueError(f"EW03 {disease}: chybí list {sheet}")
        rows = sheets[sheet]
        h = _header_row(rows, "K datu")
        header = [_text(c) for c in rows[h]]
        cols = _columns(rows[h], EW03, f"EW03 {disease} [{sheet}]")
        pos = {name: header.index(name) for name in ("NUTS", "Kraj") + (("LAU", "Okres") if level == "okres" else ())}
        for row in rows[h + 1:]:
            day = _date(row[0]) if row else None
            if day is None:
                continue
            nuts, kraj = _text(row[pos["NUTS"]]), _text(row[pos["Kraj"]])
            if level == "kraj" and not nuts:
                if kraj not in ("Česká republika", "ČR"):
                    raise ValueError(f"EW03 {disease}: řádek bez kódu NUTS, který není ČR: {kraj!r}")
                nuts = "CZ"
            rec = {"k_datu": day.isoformat(), "nemoc": disease, "uroven": level, "nuts": nuts, "kraj": kraj,
                   "lau": _text(row[pos["LAU"]]) if level == "okres" else "",
                   "okres": _text(row[pos["Okres"]]) if level == "okres" else ""}
            rec.update({col: _num(row[i]) for col, i in cols.items()})
            out.append(rec)
    return pd.DataFrame(out, columns=["k_datu", "nemoc", "uroven", "nuts", "kraj", "lau", "okres", *EW03.values()])


def parse_ew04(content: bytes, disease: str) -> pd.DataFrame:
    """Nově hlášení, hospitalizovaní a přijatí na JIP podle očkování v dané sezóně."""
    rows = next(iter(_rows(content).values()))
    h = _header_row(rows, "Datum")
    header = [_text(c) for c in rows[h]]
    cols = _columns(rows[h], EW04, f"EW04 {disease}")
    season = header.index("Sezóna")
    out = []
    for row in rows[h + 1:]:
        day = _date(row[0]) if row else None
        if day is None:
            continue
        rec = {"datum": day.isoformat(), "nemoc": disease, "sezona": _text(row[season])}
        rec.update({col: _num(row[i]) for col, i in cols.items()})
        out.append(rec)
    return pd.DataFrame(out, columns=["datum", "nemoc", "sezona", *EW04.values()])


# ── celá denní složka ────────────────────────────────────────────────────────

def classify(filename: str) -> tuple[str, str] | None:
    """„EW05_Chripka_…_20261125.xlsx“ → ("EW05", "Chripka"); jiné soubory → None."""
    m = re.fullmatch(r"(EW\d\d|eHos\d\d|eVak\d\d)_([^_]+)_.*\.xlsx", filename)
    if not m:
        return None
    kind, disease = m.groups()
    wanted = {k: d for k, _, d in WANTED}
    return (kind, disease) if kind in wanted and disease in wanted[kind] else None


def parse_folder(files: dict) -> tuple[dict, dict]:
    """
    {(druh, nemoc): obsah souboru} → ({jméno CSV: DataFrame}, stav).

    Chybějící soubor je chyba: neúplná složka by dala řady, kde jedna nemoc tiše
    zůstane na včerejšku.
    """
    expected = {(kind, d) for kind, _, diseases in WANTED for d in diseases}
    missing = sorted(expected - set(files))
    if missing:
        raise ValueError(f"ve složce chybí soubory: {missing}")

    state: dict = {"vakciny": {}}
    denni, nemocnice, ockovani, laborator, uzemi, stav_ock = [], [], [], [], [], []
    for name, key in DISEASES.items():
        df, st = parse_ew05(files[("EW05", name)], key)
        if df.empty:
            raise ValueError(f"EW05 {name}: žádné řádky")
        denni.append(df)
        for k in ("stav_k", "zpracovano"):
            if st.get(k) and state.setdefault(k, st[k]) != st[k]:
                raise ValueError(f"soubory ve složce mají různý {k}: {state[k]} a {st[k]} ({name})")
        nemocnice.append(parse_ehos03(files[("eHos03", name)], key))
        laborator.append(parse_ew07(files[("EW07", name)], key))
        uzemi.append(parse_ew03(files[("EW03", name)], key))
    for name in VACCINES:
        df, note = parse_evak(files[("eVak01", name)], files[("eVak02", name)], DISEASES[name])
        ockovani.append(df)
        state["vakciny"][DISEASES[name]] = note
    for name in VAX_STATUS:
        stav_ock.append(parse_ew04(files[("EW04", name)], DISEASES[name]))
    if "stav_k" not in state:
        raise ValueError("v souborech není „Stav k datu“ — změnil se tvar hlavičky")

    def join(parts, key, what):
        df = pd.concat(parts, ignore_index=True).drop_duplicates()
        # Po sloučení shodných řádků smí zbýt jeden na klíč; dva různé = zdroj si odporuje.
        clash = df[df.duplicated(key, keep=False)]
        if not clash.empty:
            raise ValueError(f"{what}: {len(clash)} řádků má stejný klíč {key} a různá čísla, "
                             f"např. {clash.iloc[0][key].tolist()}")
        return df.sort_values(key, kind="stable").reset_index(drop=True)

    frames = {
        "denni": join(denni, ["nemoc", "kraj_kod", "datum", "vek"], "EW05"),
        "nemocnice": join(nemocnice, ["nemoc", "kraj_kod", "datum"], "eHos03"),
        "ockovani": join(ockovani, ["nemoc", "rozmer", "datum", "kategorie"], "eVak"),
        "laboratore": join(laborator, ["nemoc", "datum"], "EW07"),
        "uzemi": join(uzemi, ["nemoc", "uroven", "nuts", "lau"], "EW03"),
        "ockovani_stav": join(stav_ock, ["nemoc", "datum"], "EW04"),
    }
    state["rozsah"] = {"od": frames["denni"]["datum"].min(), "do": frames["denni"]["datum"].max()}
    return frames, state


# ── sdílená složka ───────────────────────────────────────────────────────────

def _propfind(path: str) -> list[tuple[str, bool]]:
    """[(cesta, je_složka)] pro přímé potomky `path` na sdíleném úložišti."""
    url = DAV + urllib.parse.quote(path)
    resp = requests.request("PROPFIND", url, headers={**HEADERS, "Depth": "1"}, timeout=TIMEOUT)
    resp.raise_for_status()
    out = []
    for item in re.findall(r"<d:response>(.*?)</d:response>", resp.text, re.S):
        href = urllib.parse.unquote(re.search(r"<d:href>(.*?)</d:href>", item).group(1))
        rel = href.split(SHARE_TOKEN, 1)[-1]
        if rel.rstrip("/") != path.rstrip("/"):
            out.append((rel, "<d:collection" in item))
    return out


def newest_folder(listing: list[tuple[str, bool]]) -> str:
    """Jméno nejnovější denní složky „RRRR-MM-DD“ z výpisu kořene."""
    days = sorted(p.strip("/") for p, is_dir in listing
                  if is_dir and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.strip("/")))
    if not days:
        raise ValueError("ve sdílené složce nejsou denní složky RRRR-MM-DD — změnila se struktura úložiště")
    return days[-1]


def _fetch_folder(day: str) -> dict:
    """Stáhne z denní složky jen soubory, které se čtou. {(druh, nemoc): bytes}"""
    files = {}
    reports = sorted({report for _, report, _ in WANTED})
    for report in reports:
        for disease_dir, is_dir in _propfind(f"/{day}/{report}/"):
            if not is_dir:
                continue
            for path, is_sub in _propfind(disease_dir):
                key = None if is_sub else classify(path.rsplit("/", 1)[-1])
                if key is None:
                    continue
                resp = requests.get(DAV + urllib.parse.quote(path), headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                files[key] = resp.content
                time.sleep(PAUSE)
    return files


def download(output_dir: Path, today: date | None = None) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    state_path = output_dir / STATE_NAME
    paths = [output_dir / name for name in CSV.values()]
    # Výpis kořene je dotaz na zdroj: i když nic nového není, zdroj jsme ověřili.
    download.source_checked = True

    day = newest_folder(_propfind("/"))
    age = (today - date.fromisoformat(day)).days
    if age > MAX_AGE_DAYS:
        raise ValueError(f"nejnovější denní složka je {day} ({age} dní stará) — zdroj se neobnovuje")

    if state_path.exists() and all(p.exists() for p in paths):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("slozka") == day:
            print(f"  [uzis_esurveillance] složka {day} už je stažená (stav k {state.get('stav_k')}) — nestahuji")
            return [str(p) for p in paths] + [str(state_path)]

    print(f"  [uzis_esurveillance] stahuji složku {day}")
    frames, state = parse_folder(_fetch_folder(day))
    state = {"slozka": day, **state,
             "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for key, name in CSV.items():
        frames[key].to_csv(output_dir / name, index=False, encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    d = frames["denni"]
    print(f"  [uzis_esurveillance] složka {day}, stav k {state['stav_k']}: "
          f"{d['nemoc'].nunique()} nemoci, denní řada {state['rozsah']['od']} až {state['rozsah']['do']}, "
          f"{len(d):,} řádků → {output_dir}")
    return [str(p) for p in paths] + [str(state_path)]


if __name__ == "__main__":
    download(Path(__file__).resolve().parents[2] / "data" / "uzis_esurv")

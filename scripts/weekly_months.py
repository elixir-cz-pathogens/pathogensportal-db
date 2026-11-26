"""
Týdenní souhrn ÚZIS → měsíční řady pro detektor signálů.

Detektor (detect_anomalies.py) hodnotí kalendářní měsíce. Týdenní souhrn má ISO
týdny a týden na přelomu měsíce patří do dvou měsíců. Tenhle modul ho mezi ně
dělí podle pracovních dnů (pondělí až pátek bez státních svátků), ne podle sedmi
kalendářních dnů: hlásí se v pracovní dny.

Změřeno proti otevřenému měsíčnímu CSV ISIN (sedm diagnóz se stejným kódem,
celá ČR, 2/2022–12/2025, měsíce s 20 a více případy) — o kolik se přepočtený
měsíc liší od skutečného:

    týden celý do měsíce svého čtvrtka    medián 7 %,   devět měsíců z deseti do 17 %
    dělení podle 7 kalendářních dnů       medián 4,4 %, do 8,7 %
    dělení podle pracovních dnů           medián 1,8 %, do 4,7 %
    … a bez státních svátků               medián 1,6 %, do 3,9 %

Měsíční počty z tohoto modulu jsou tedy ODHAD, ne úřední měsíční statistika.
Proto se jimi nedoplňuje otevřené CSV: celá řada pro detektor — historie i
hodnocený měsíc — vzniká stejným převodem, aby se jeho chyba neprojevila jako
rozdíl mezi „dřív“ a „teď“.

Dělí se celé případy (metodou největšího zbytku), takže součet týdne zůstane
přesně zachován a v měsíci nevznikne 12,6 případu. Záleží na tom hlavně u
vzácných nemocí, kde detektor hlásí už dva případy v měsíci.
"""

from calendar import monthrange
from datetime import date, timedelta

import numpy as np
import pandas as pd

TOTAL = "Celkem"


def _easter(year: int) -> date:
    """Velikonoční neděle (gregoriánský výpočet, Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def public_holidays(year: int) -> set[date]:
    """Státní svátky a ostatní svátky ČR (zákon č. 245/2000 Sb.; Velký pátek od roku 2016)."""
    easter = _easter(year)
    fixed = [(1, 1), (5, 1), (5, 8), (7, 5), (7, 6), (9, 28), (10, 28), (11, 17),
             (12, 24), (12, 25), (12, 26)]
    return {date(year, m, d) for m, d in fixed} | {easter - timedelta(days=2), easter + timedelta(days=1)}


def is_working_day(day: date) -> bool:
    return day.weekday() < 5 and day not in public_holidays(day.year)


def week_shares(iso_year: int, iso_week: int) -> list[tuple[int, int, float]]:
    """
    Jak se ISO týden dělí mezi měsíce: [(rok, měsíc, podíl), …], podíly dávají 1.

    Podíl = pracovní dny týdne v daném měsíci / pracovní dny týdne celkem. Týden
    bez jediného pracovního dne v ČR nenastane; kdyby nastal, dělí se podle
    kalendářních dnů, ať se případy neztratí.
    """
    monday = date.fromisocalendar(int(iso_year), int(iso_week), 1)
    days = [monday + timedelta(days=i) for i in range(7)]
    used = [d for d in days if is_working_day(d)] or days
    counts: dict[tuple[int, int], int] = {}
    for d in used:
        counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
    return [(y, m, n / len(used)) for (y, m), n in sorted(counts.items())]


def split_count(n: int, ews: int, share_first: float) -> tuple[int, int, int, int]:
    """
    Rozdělí týden (n případů, z toho ews z EWS) mezi dva měsíce → (n1, ews1, n2, ews2).

    Celá čísla, součty zůstávají, v žádném měsíci není EWS víc než všech případů.
    Polovina jde do prvního měsíce (pevné pravidlo, ať je výstup opakovatelný).
    """
    n1 = int(np.floor(n * share_first + 0.5))
    n2 = n - n1
    e1 = int(np.floor(ews * share_first + 0.5))
    e1 = max(e1, ews - n2)      # druhý měsíc nesmí dostat víc EWS, než má případů
    e1 = min(e1, n1, ews)
    return n1, e1, n2, ews - e1


def _last_working_day(year: int, month: int) -> date:
    day = date(year, month, monthrange(year, month)[1])
    while not is_working_day(day):
        day -= timedelta(days=1)
    return day


def _first_working_day(year: int, month: int) -> date:
    day = date(year, month, 1)
    while not is_working_day(day):
        day += timedelta(days=1)
    return day


def complete_months(first_monday: date, data_until: date) -> list[tuple[int, int]]:
    """
    Měsíce, které týdenní data pokrývají celé: od prvního pondělí v datech do dne
    `data_until` (poslední den, za který už jsou hlášení načtená).

    Měří se pracovními dny — leden 2022 je celý, i když data začínají až 3. ledna,
    protože 1. a 2. ledna byl víkend.
    """
    out = []
    y, m = first_monday.year, first_monday.month
    while (y, m) <= (data_until.year, data_until.month):
        if _first_working_day(y, m) >= first_monday and _last_working_day(y, m) <= data_until:
            out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def to_months(weekly: pd.DataFrame, data_until: date) -> tuple[pd.DataFrame, list[tuple[int, int]]]:
    """
    Týdenní tabulka (diagnoza, kraj_kod, rok, tyden, vek, pocet, pocet_ews) →
    měsíční (diagnoza, kraj_kod, rok, mesic, pocet_pripadu, ews) jen za úplné měsíce,
    a seznam těch měsíců.

    Bere jen řádky „Celkem“; věkové skupiny detektor nepoužívá.
    """
    w = weekly[weekly["vek"] == TOTAL]
    if w.empty:
        raise ValueError("v týdenní tabulce nejsou řádky „Celkem“")

    weeks = w[["rok", "tyden"]].drop_duplicates()
    first_monday = min(date.fromisocalendar(int(r), int(t), 1) for r, t in weeks.itertuples(index=False))
    months = complete_months(first_monday, data_until)
    if not months:
        raise ValueError("týdenní data nepokrývají žádný celý měsíc")

    shares = {(int(r), int(t)): week_shares(r, t) for r, t in weeks.itertuples(index=False)}
    rows = []
    for dg, kraj, rok, tyden, n, ews in w[["diagnoza", "kraj_kod", "rok", "tyden", "pocet", "pocet_ews"]].itertuples(index=False):
        parts = shares[(int(rok), int(tyden))]
        if len(parts) == 1:
            (y, m, _), = parts
            rows.append((dg, kraj, y, m, int(n), int(ews)))
            continue
        (y1, m1, s1), (y2, m2, _) = parts
        n1, e1, n2, e2 = split_count(int(n), int(ews), s1)
        rows.append((dg, kraj, y1, m1, n1, e1))
        rows.append((dg, kraj, y2, m2, n2, e2))

    out = (pd.DataFrame(rows, columns=["diagnoza", "kraj_kod", "rok", "mesic", "pocet_pripadu", "ews"])
           .groupby(["diagnoza", "kraj_kod", "rok", "mesic"], as_index=False).sum())
    keep = pd.MultiIndex.from_frame(out[["rok", "mesic"]]).isin(months)
    return out[keep].reset_index(drop=True), months

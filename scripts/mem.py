"""
Moving Epidemic Method (MEM) — sezónní epidemický práh a prahy intenzity.

Metoda: Vega et al. 2013 (epidemický práh) a 2015 (intenzita); standard ECDC
a WHO PISA pro chřipkovou surveillance. Referenční implementací je R balík
`mem` (Lozano) — tohle je nezávislá reimplementace v numpy se stejnými výchozími
parametry, ověřená proti němu v tests/test_mem_golden.py. Jen výpočet, žádné
I/O: vstupem je matice týden × sezóna, odkud ta čísla jsou, řeší compute_mem.py.

Jak to funguje:

1.  V každé historické sezóně se najde epidemické období. Pro každou délku r se
    vezme r po sobě jdoucích týdnů s největším součtem a spočítá se, kolik
    procent celé sezóny pokrývají (křivka MAP). Křivka roste a zplošťuje se;
    epidemie má délku posledního r, kde přidaný týden ještě přinesl aspoň δ %
    sezóny (default 2,8). Přírůstky se před tím vyhladí, jinak by o délce
    rozhodl jeden zubatý týden.
2.  Epidemický práh: z týdnů PŘED epidemií se z každé sezóny vezme n nejvyšších
    (n ≈ 30 / počet sezón) a práh je průměr + 1,645·sd — hodnota, kterou klidný
    týden překročí jen v 5 % případů.
3.  Prahy intenzity: totéž z n nejvyšších týdnů BĚHEM epidemie, na logaritmické
    škále (vrcholy jsou zhruba lognormální), kvantily 40 / 90 / 97,5 %.
    Pozor, 40. percentil leží POD geometrickým průměrem: mezi vrcholovými týdny
    má být ~40 % „nízkých“, 50 % „středních“, 7,5 % „vysokých“ a 2,5 % „velmi
    vysokých“.

Odchylka od R balíku: chybějící týdny uvnitř sezóny interpolujeme lineárně.
Balík je doplňuje jádrovým vyhlazením s šířkou pásma volenou cross-validací;
u jednotlivých děr je rozdíl zanedbatelný a lineární interpolace je
deterministická a vysvětlitelná.
"""

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np

DELTA = 2.8                          # % sezóny; pod tím už přidaný týden do epidemie nepatří
THRESHOLD_LEVEL = 0.95               # epidemický práh: jednostranný 95% interval
INTENSITY_LEVELS = (0.40, 0.90, 0.975)
TARGET_VALUES = 30                   # kolik hodnot má jít do odhadu napříč sezónami
MAX_SEASONS = 10                     # starší sezóny už dnešní surveillance nepopisují
SMOOTH_BANDWIDTH = 1.0               # týdny; vyhlazení přírůstků křivky MAP

LEVELS = ("baseline", "low", "medium", "high", "very_high")


@dataclass
class MemModel:
    epidemic_threshold: float
    intensity_thresholds: tuple[float, float, float]   # medium, high, very high
    delta: float
    n_values: int                                      # n nejvyšších hodnot na sezónu
    seasons: list[str]
    periods: dict[str, tuple[int, int]] = field(default_factory=dict)  # sezóna → (start, end), 0-based včetně


def fill_inner_gaps(x: np.ndarray) -> np.ndarray:
    """Lineárně doplní NaN uvnitř řady; okraje (sezóna ještě nezačala/neskončila) nechá."""
    x = np.asarray(x, dtype=float).copy()
    known = np.flatnonzero(~np.isnan(x))
    if len(known) < 2:
        return x
    inner = np.arange(known[0], known[-1] + 1)
    x[inner] = np.interp(inner, known, x[known])
    return x


def map_curve(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Pro r = 1..n: největší podíl sezóny (v %) v r po sobě jdoucích týdnech
    a začátek toho okna. Při shodě vyhrává dřívější okno.
    """
    x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0)
    n = len(x)
    csum = np.concatenate([[0.0], np.cumsum(x)])
    total = csum[-1]
    pct = np.zeros(n)
    start = np.zeros(n, dtype=int)
    for r in range(1, n + 1):
        sums = csum[r:] - csum[:-r]
        start[r - 1] = int(np.argmax(sums))
        pct[r - 1] = 100.0 * sums[start[r - 1]] / total if total > 0 else 0.0
    return pct, start


def smooth(y: np.ndarray, h: float = SMOOTH_BANDWIDTH) -> np.ndarray:
    """Lokální lineární regrese s gaussovským jádrem, vyhodnocená v každém bodě řady."""
    y = np.asarray(y, dtype=float)
    pos = np.arange(len(y), dtype=float)
    out = np.empty(len(y))
    for i, x0 in enumerate(pos):
        d = pos - x0
        w = np.exp(-0.5 * (d / h) ** 2)
        s0, s1, s2 = w.sum(), (w * d).sum(), (w * d * d).sum()
        out[i] = ((s2 * (w * y).sum() - s1 * (w * d * y).sum()) / (s0 * s2 - s1 * s1))
    return np.maximum(out, 0.0)


def epidemic_period(x: np.ndarray, delta: float = DELTA) -> tuple[int, int]:
    """Začátek a konec epidemického období (indexy týdnů, 0-based, včetně)."""
    pct, start = map_curve(x)
    n = len(pct)
    gains = np.diff(smooth(np.concatenate([[0.0], pct])))    # gains[r-1] = přínos r-tého týdne
    below = np.flatnonzero(gains < delta)
    duration = max(int(below[0]), 1) if len(below) else n    # poslední r s přínosem ≥ δ
    s = int(start[duration - 1])
    return s, s + duration - 1


def _top(values: np.ndarray, n: int) -> np.ndarray:
    values = values[np.isfinite(values)]
    return np.sort(values)[::-1][:n]


def _upper_limit(values: np.ndarray, level: float, log_scale: bool) -> float:
    """Horní mez jednostranného intervalu pro jednotlivé pozorování (ne pro průměr)."""
    z = NormalDist().inv_cdf(level)
    if not log_scale:
        return float(values.mean() + z * values.std(ddof=1))
    shift = 0.0 if np.all(values != 0) else 1.0              # log(0) — posun o 1 jako v R balíku
    lx = np.log(values + shift)
    return float(np.exp(lx.mean() + z * lx.std(ddof=1)) - shift)


def fit(matrix: np.ndarray, seasons: list[str], delta: float = DELTA,
        max_seasons: int | None = MAX_SEASONS) -> MemModel:
    """
    matrix: týdny v řádcích, sezóny ve sloupcích (stejné pořadí jako `seasons`,
    od nejstarší). Sezóny bez jediné nenulové hodnoty se zahodí.
    """
    matrix = np.asarray(matrix, dtype=float)
    keep = [j for j in range(matrix.shape[1]) if np.nansum(matrix[:, j]) > 0]
    if max_seasons:
        keep = keep[-max_seasons:]
    if len(keep) < 2:
        raise ValueError("MEM potřebuje aspoň dvě sezóny s nenulovými daty")

    n_values = max(1, round(TARGET_VALUES / len(keep)))
    pre, epi, periods = [], [], {}
    for j in keep:
        x = fill_inner_gaps(matrix[:, j])
        s, e = epidemic_period(x, delta)
        periods[seasons[j]] = (s, e)
        pre.append(_top(x[:s], n_values))
        epi.append(_top(x[s:e + 1], n_values))
    pre, epi = np.concatenate(pre), np.concatenate(epi)
    if len(pre) < 2:
        raise ValueError("Příliš málo pre-epidemických týdnů — řada začíná až v epidemii?")

    return MemModel(
        epidemic_threshold=_upper_limit(pre, THRESHOLD_LEVEL, log_scale=False),
        intensity_thresholds=tuple(_upper_limit(epi, lv, log_scale=True) for lv in INTENSITY_LEVELS),
        delta=delta, n_values=n_values,
        seasons=[seasons[j] for j in keep], periods=periods,
    )


def classify(value: float, model: MemModel) -> str:
    """Pásmo týdne: nejvyšší práh, který hodnota dosáhla."""
    bounds = (model.epidemic_threshold, *model.intensity_thresholds)
    return LEVELS[sum(value >= b for b in bounds)]

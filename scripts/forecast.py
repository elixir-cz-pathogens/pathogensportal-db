"""
Práce s kvantilovou předpovědí — pravděpodobnost překročení prahu a zpětné vyhodnocení.

Předpovědní huby (RespiCast, FluSight) nedávají rozdělení, ale jeho kvantily
(23 hladin od 1 % do 99 %). Jen výpočet, žádné I/O — stejně jako mem.py.
"""

import numpy as np

# Pod 1. a nad 99. percentil předpověď nic neříká; tvrdit 0 % nebo 100 % by byla
# jistota, kterou v datech nemáme.
P_FLOOR, P_CEIL = 0.01, 0.99


def prob_at_least(quantile_levels, quantile_values, threshold: float) -> float:
    """
    P(X ≥ threshold) z kvantilů: distribuční funkce lineárně mezi sousedními
    kvantily, mimo jejich rozsah oříznutá na 1 % / 99 %.
    """
    q = np.asarray(quantile_levels, dtype=float)
    v = np.asarray(quantile_values, dtype=float)
    order = np.argsort(q)
    q, v = q[order], np.maximum.accumulate(v[order])     # kvantily z ensemble nemusí být přesně monotónní
    if threshold <= v[0]:
        return P_CEIL
    if threshold > v[-1]:
        return P_FLOOR
    # np.interp potřebuje rostoucí x; u shodných hodnot (časté u nul) bere pravý okraj
    cdf = float(np.interp(threshold, v, q))
    return float(np.clip(1.0 - cdf, P_FLOOR, P_CEIL))


def interval_coverage(lower, upper, observed) -> float:
    """Podíl případů, kdy skutečnost padla do intervalu [lower, upper]."""
    lower, upper, observed = (np.asarray(a, dtype=float) for a in (lower, upper, observed))
    return float(((observed >= lower) & (observed <= upper)).mean())

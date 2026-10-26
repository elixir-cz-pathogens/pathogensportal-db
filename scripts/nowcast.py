"""
Nowcast laboratorních detekcí — odhad konečného počtu za týdny, které laboratoře
ještě dohlašují. Jen výpočet, žádné I/O — stejně jako mem.py a forecast.py.

Problém: číslo za týden se po prvním zveřejnění ještě 2–3 týdny doplňuje. V ERVISS
snapshotech (= data SZÚ) bývá první číslo za chřipku asi 78 % konečného, za RSV
asi 73 %; SARS-CoV-2 je kompletní hned. Graf tak na konci vždycky „padá“.

Metoda — chain-ladder (Mack 1993), jak se používá pro nowcasting v epidemiologii
(baselinenowcast, RKI):
  N(t, d)   počet za týden t, jak byl známý d týdnů po jeho konci (d = 0 je první
            pátek po skončení týdne)
  f_d       = Σ_t N(t, d+1) / Σ_t N(t, d)  přes týdny, kde jsou známé obě hodnoty
            (poměr součtů, ne průměr poměrů — u malých počtů stabilnější)
  F_d       = f_d · f_{d+1} · … · f_{D-1}  doplňovací faktor do konečné hodnoty
  zbytek    ~ NegBin(μ = N(t, d) · (F_d − 1), φ_d); φ_d metodou momentů z týdnů,
            jejichž konečná hodnota je už známá
  odhad     = N(t, d) + zbytek → kvantily z losování; nikdy pod nahlášeným číslem

Rozsah (podle backtestu na snapshotech ERVISS 2024/25–2025/26, docs/analytics/nowcasting.md):
jen zpoždění 0–1 a jen v sezóně (týden 40–20). Starší týdny jsou skoro úplné
a mimo sezónu jde o jednotky případů — tam odhad chybu nesnižoval, místy zvyšoval.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from forecast import weighted_interval_score

D_FINAL = 8                  # po tolika týdnech se číslo už prakticky nemění
WINDOW_WEEKS = 52            # tréninkové okno: poslední rok týdnů
NOWCAST_DELAYS = (0, 1)
LEVELS = (0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975)
N_DRAWS = 2000
PHI_BOUNDS = (0.5, 1e4)      # φ → ∞ je Poisson; dolní mez brání absurdně širokým pásům


def delay_weeks(week_start: date, as_of: date) -> int:
    """Zpoždění v týdnech: 0 = do pátku po skončení týdne, záporné = týden ještě běží."""
    return ((as_of - week_start).days - 7) // 7


def in_season(week_start: date) -> bool:
    w = week_start.isocalendar().week
    return w >= 40 or w <= 20


def triangle(obs: pd.DataFrame) -> pd.DataFrame:
    """
    `obs`: sloupce week (date, pondělí týdne), snapshot (date), value.
    Vrátí dlouhý trojúhelník week, d, snapshot, value pro 0 ≤ d ≤ D_FINAL;
    když do jednoho zpoždění spadnou dva snapshoty, platí pozdější.
    """
    t = obs.copy()
    t["d"] = [delay_weeks(w, s) for w, s in zip(t["week"], t["snapshot"])]
    t = t[(t["d"] >= 0) & (t["d"] <= D_FINAL)].dropna(subset=["value"])
    return (t.sort_values("snapshot").groupby(["week", "d"], as_index=False).last()
            [["week", "d", "snapshot", "value"]])


@dataclass
class Model:
    factors: dict = field(default_factory=dict)      # d → F_d
    dispersion: dict = field(default_factory=dict)   # d → φ_d
    n_weeks: dict = field(default_factory=dict)      # d → kolik týdnů dalo φ_d


def fit(tri: pd.DataFrame, as_of: date) -> Model:
    """Faktory a disperze jen z toho, co bylo známé k `as_of` (žádný pohled do budoucna)."""
    start = as_of - timedelta(weeks=WINDOW_WEEKS + D_FINAL)
    known = tri[(tri["snapshot"] <= as_of) & (tri["week"] >= start)]
    wide = known.pivot_table(index="week", columns="d", values="value", aggfunc="first")

    f = {}
    for d in range(D_FINAL):
        if d in wide and d + 1 in wide:
            pair = wide[[d, d + 1]].dropna()
            f[d] = pair[d + 1].sum() / pair[d].sum() if pair[d].sum() > 0 else 1.0
        else:
            f[d] = 1.0
    model = Model(factors={d: float(np.prod([f[k] for k in range(d, D_FINAL)]))
                           for d in range(D_FINAL)})

    for d in NOWCAST_DELAYS:
        if d not in wide or D_FINAL not in wide:
            model.dispersion[d], model.n_weeks[d] = PHI_BOUNDS[1], 0
            continue
        pair = wide[[d, D_FINAL]].dropna()
        mu = pair[d] * (model.factors[d] - 1)
        rest = pair[D_FINAL] - pair[d]
        excess = float(((rest - mu) ** 2 - mu).sum())     # rozptyl nad Poissonův
        phi = float((mu ** 2).sum()) / excess if excess > 0 else PHI_BOUNDS[1]
        model.dispersion[d] = float(np.clip(phi, *PHI_BOUNDS))
        model.n_weeks[d] = int(len(pair))
    return model


def quantiles(reported: float, model: Model, d: int, rng: np.random.Generator) -> np.ndarray:
    """Kvantily LEVELS odhadu konečného počtu; dolní mez je nahlášené číslo."""
    mu = max(reported * (model.factors.get(d, 1.0) - 1.0), 0.0)
    if mu == 0:
        return np.full(len(LEVELS), float(reported))
    phi = model.dispersion.get(d, PHI_BOUNDS[1])
    draws = reported + rng.negative_binomial(phi, phi / (phi + mu), size=N_DRAWS)
    return np.quantile(draws, LEVELS)


def backtest(tri: pd.DataFrame, min_history_weeks: int = 40, seed: int = 1) -> pd.DataFrame:
    """
    Zpětné vyhodnocení jako v reálném čase: v každém snapshotu jen data známá k tomu
    dni, odhad pro týdny se zpožděním NOWCAST_DELAYS v sezóně, srovnání s konečnou
    hodnotou (zpoždění D_FINAL). Alternativa „nic nedělat“ = nahlášené číslo; její
    WIS je absolutní chyba.
    """
    rng = np.random.default_rng(seed)
    final = tri[tri["d"] == D_FINAL].set_index("week")["value"]
    by_snap = tri.set_index(["snapshot", "week"])["value"]
    snaps = sorted(tri["snapshot"].unique())
    if not snaps:
        return pd.DataFrame()
    first = snaps[0]
    rows = []
    for s in snaps:
        if s < first + timedelta(weeks=min_history_weeks):
            continue
        model = fit(tri, s)
        for (snap, week), reported in by_snap.loc[[s]].items():
            d = delay_weeks(week, snap)
            if d not in NOWCAST_DELAYS or not in_season(week) or week not in final.index:
                continue
            q = quantiles(reported, model, d, rng)
            y = float(final[week])
            rows.append({"origin": s, "week": week, "d": d, "reported": float(reported),
                         "final": y, **{f"q{lv}": v for lv, v in zip(LEVELS, q)},
                         "wis_nowcast": weighted_interval_score(LEVELS, q, y),
                         "wis_reported": abs(y - reported)})
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame) -> dict:
    """Souhrn backtestu nebo stínového vyhodnocení: n, WIS, zlepšení, pokrytí pásů."""
    if results.empty:
        return {"n": 0}
    wn, wr = results["wis_nowcast"].mean(), results["wis_reported"].mean()

    def covered(lo, hi):
        return float(((results["final"] >= results[f"q{lo}"])
                      & (results["final"] <= results[f"q{hi}"])).mean())

    return {
        "n": int(len(results)),
        "wis_nowcast": round(float(wn), 2),
        "wis_reported": round(float(wr), 2),
        "improvement": round(float(1 - wn / wr), 3) if wr > 0 else None,
        "coverage_50": round(covered(0.25, 0.75), 3),
        "coverage_95": round(covered(0.025, 0.975), 3),
    }

"""MEM proti referenci — R balík `mem` 2.19.

Referenční čísla v fixtures/mem_reference.json vznikla jednorázově spuštěním R
(memmodel/memtiming s výchozími parametry) nad týmiž maticemi, které leží vedle.
R v CI nemáme a mít nechceme; test hlídá, že se reimplementace od reference
neodchýlí — třeba při „nevinné“ úpravě vyhlazení nebo výběru hodnot.

  mem_ili_cz.csv     česká ILI na 100 tis. (WHO FluID), 10 sezón — reálný vstup
  mem_synthetic.csv  zubaté Poissonovy sezóny s nulami — cesty, kterými česká
                     data neprojdou (shody oken, nulové týdny před sezónou)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import mem

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "mem_reference.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['file']}-delta{c['delta']}")
def test_thresholds_and_periods_match_r(case):
    data = pd.read_csv(FIXTURES / case["file"], index_col=0)
    model = mem.fit(data.to_numpy(), list(data.columns), delta=case["delta"])

    assert model.n_values == case["n_values"]
    assert model.epidemic_threshold == pytest.approx(case["epidemic"], rel=1e-8)
    assert model.intensity_thresholds == pytest.approx(case["intensity"], rel=1e-8)
    periods = [[s + 1, e + 1] for s, e in model.periods.values()]   # R čísluje od 1
    assert periods == case["periods"]


def test_smoothing_matches_sm_regression():
    # sm::sm.regression(h = 1) na typické křivce MAP; záporný odhad se ořezává na 0
    y = np.array([0, 5, 30, 62, 80, 88, 93, 96, 98, 100], dtype=float)
    expected = [0.0, 11.3889241848, 32.7164699035, 56.7930610772, 75.3127664718,
                86.0799438410, 91.9714681344, 95.5036805542, 97.9503777336, 100.0144995070]
    assert mem.smooth(y) == pytest.approx(expected, abs=1e-8)


def test_log_limit_with_zeros_matches_r():
    # iconfianza.logx(c(0,3,12,40,7,0,25), 0.90, colas = 1): nula → posun o 1
    values = np.array([0, 3, 12, 40, 7, 0, 25], dtype=float)
    assert mem._upper_limit(values, 0.90, log_scale=True) == pytest.approx(41.5040615040, rel=1e-9)


def test_medium_threshold_lies_below_geometric_mean():
    # 40. percentil, ne 60. — splést znaménko z je nejsnazší chyba v celé metodě
    data = pd.read_csv(FIXTURES / "mem_ili_cz.csv", index_col=0)
    model = mem.fit(data.to_numpy(), list(data.columns))
    top = np.concatenate([np.sort(data[c].to_numpy())[::-1][:model.n_values] for c in data.columns])
    assert model.intensity_thresholds[0] < np.exp(np.log(top).mean())


def test_classify_bands():
    model = mem.MemModel(50.0, (130.0, 340.0, 520.0), 2.8, 3, [])
    assert [mem.classify(v, model) for v in (5, 50, 129.9, 130, 400, 900)] == \
        ["baseline", "low", "low", "medium", "high", "very_high"]


def test_inner_gaps_interpolated_edges_kept():
    x = mem.fill_inner_gaps(np.array([np.nan, 2.0, np.nan, np.nan, 8.0, np.nan]))
    assert np.isnan(x[0]) and np.isnan(x[-1])
    assert x[1:5] == pytest.approx([2.0, 4.0, 6.0, 8.0])


def test_needs_two_seasons():
    with pytest.raises(ValueError, match="dvě sezóny"):
        mem.fit(np.ones((33, 1)), ["2024/25"])

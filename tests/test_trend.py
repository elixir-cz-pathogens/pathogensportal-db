"""Trend růstu/poklesu — kategorie, jistota a okrajové případy."""
import numpy as np
import pytest

import trend

POP = [1e6, 1e6, 1e6]


def test_t_cdf_matches_tables():
    assert trend._t_cdf(0.0, 3) == pytest.approx(0.5)
    assert trend._t_cdf(3.182, 3) == pytest.approx(0.975, abs=1e-3)    # tabulková hodnota t(3)
    assert trend._t_cdf(-6.314, 1) == pytest.approx(0.05, abs=1e-3)    # t(1) = Cauchy


def test_clean_exponential_growth():
    r = trend.growth([100, 130, 169], POP)
    assert r["category"] == "growing"
    assert r["weekly_change"] == pytest.approx(0.30, abs=0.01)
    assert r["doubling_weeks"] == pytest.approx(np.log(2) / np.log(1.3), abs=0.1)


def test_decline_mirrors_growth():
    r = trend.growth([169, 130, 100], POP)
    assert r["category"] == "declining" and r["weekly_change"] < 0


def test_zigzag_is_not_called_a_trend():
    # nahoru-dolů: sklon ~0 a velká rezidua → nejistota, ne „roste“
    assert trend.growth([200, 320, 210], POP)["category"] == "stable"


def test_population_change_is_not_growth():
    # případů 2× víc jen proto, že se zdvojnásobila pokrytá populace (FluID 2025/26)
    r = trend.growth([100, 100, 200], [1e6, 1e6, 2e6])
    assert abs(r["weekly_change"]) < 0.01


def test_incomplete_or_empty_window():
    assert trend.growth([100, np.nan, 120], POP) is None
    assert trend.growth([0, 0, 0], POP) is None
    assert trend.growth([0, 3, 9], POP)["category"] in ("growing", "likely_growing")   # nula nespadne na log(0)

"""Pravděpodobnost překročení prahu z kvantilové předpovědi."""
import numpy as np
import pytest

import forecast

LEVELS = [0.025, 0.25, 0.5, 0.75, 0.975]
VALUES = [10.0, 30.0, 40.0, 50.0, 90.0]


def test_threshold_at_median_gives_half():
    assert forecast.prob_at_least(LEVELS, VALUES, 40.0) == pytest.approx(0.5)


def test_interpolates_between_quantiles():
    # 45 leží v půli mezi mediánem (0,5) a 75. percentilem → F = 0,625
    assert forecast.prob_at_least(LEVELS, VALUES, 45.0) == pytest.approx(0.375)


def test_never_claims_certainty_outside_forecast_range():
    assert forecast.prob_at_least(LEVELS, VALUES, 1.0) == forecast.P_CEIL
    assert forecast.prob_at_least(LEVELS, VALUES, 500.0) == forecast.P_FLOOR


def test_handles_non_monotone_and_tied_quantiles():
    # ensemble občas vrátí kvantily o chlup nemonotónní; dole bývají shodné nuly
    p = forecast.prob_at_least([0.025, 0.25, 0.5, 0.75, 0.975], [0.0, 0.0, 6.2, 6.1, 16.0], 49.4)
    assert p == forecast.P_FLOOR


def test_interval_coverage():
    assert forecast.interval_coverage([0, 0, 0, 0], [10, 10, 10, 10], [5, 11, 10, -1]) == 0.5

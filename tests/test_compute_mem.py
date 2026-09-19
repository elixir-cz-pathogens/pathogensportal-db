"""Sestavení matice týden × sezóna pro MEM — hranice sezóny a týden 53."""
import pandas as pd

import compute_mem


def _series(rows):
    df = pd.DataFrame(rows, columns=["rok", "tyden", "mira"])
    df["sezona"] = df["rok"].where(df["tyden"] >= 27, df["rok"] - 1)
    return df


def test_season_spans_new_year():
    wide = compute_mem.season_matrix(_series([(2024, 40, 10.0), (2025, 5, 200.0), (2025, 40, 7.0)]))
    assert list(wide.index) == compute_mem.SEASON_WEEKS
    assert wide.loc[40, 2024] == 10.0 and wide.loc[5, 2024] == 200.0
    assert wide.loc[40, 2025] == 7.0


def test_week_53_averaged_into_52():
    wide = compute_mem.season_matrix(_series([(2015, 52, 100.0), (2015, 53, 60.0)]))
    assert wide.loc[52, 2015] == 80.0
    assert 53 not in wide.index


def test_summer_weeks_outside_season_dropped():
    wide = compute_mem.season_matrix(_series([(2025, 30, 3.0), (2025, 41, 9.0)]))
    assert wide[2025].notna().sum() == 1


def _bell(peak_height: float, peak_at: int = 18) -> list[float]:
    t = pd.Series(range(33), dtype=float)
    return list(5.0 + peak_height * (-0.5 * ((t - peak_at) / 3.0) ** 2).apply("exp"))


def test_flat_season_detected_as_no_epidemic():
    # čtyři běžné sezóny a jedna plochá (vrchol 5 + 20 = 25, pod prahem ostatních)
    heights = {2015: 250.0, 2016: 180.0, 2017: 20.0, 2018: 300.0, 2019: 220.0}
    wide = pd.DataFrame({y: _bell(h) for y, h in heights.items()}, index=compute_mem.SEASON_WEEKS)
    dropped = compute_mem.seasons_without_epidemic(wide, list(heights), delta=2.8)
    assert list(dropped) == [2017]
    assert "bez epidemie" in dropped[2017]


def test_mild_but_real_season_is_kept():
    heights = {2015: 250.0, 2016: 80.0, 2017: 180.0, 2018: 300.0}
    wide = pd.DataFrame({y: _bell(h) for y, h in heights.items()}, index=compute_mem.SEASON_WEEKS)
    assert compute_mem.seasons_without_epidemic(wide, list(heights), delta=2.8) == {}


def test_delta_grid_skips_values_unusable_on_flat_series():
    # skoro plochá řada (jako ARI): při malém δ vyjde epidemie od 1. týdne a nezbudou
    # pre-epidemické hodnoty — takové δ se má přeskočit, ne shodit celý výpočet
    flat = {y: [800.0 + 0.4 * v for v in _bell(h)] for y, h in
            {2015: 900.0, 2016: 700.0, 2017: 1000.0, 2018: 800.0}.items()}
    wide = pd.DataFrame(flat, index=compute_mem.SEASON_WEEKS)
    best, grid = compute_mem.optimize_delta(wide.to_numpy(), [str(y) for y in wide.columns])
    assert 0 < len(grid) < len(compute_mem.DELTA_GRID)
    assert best in [g["delta"] for g in grid]

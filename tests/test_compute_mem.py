"""Sestavení matice týden × sezóna pro MEM — hranice sezóny a týden 53."""
import pandas as pd

import compute_mem


def _series(rows):
    df = pd.DataFrame(rows, columns=["rok", "tyden", "ili"])
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

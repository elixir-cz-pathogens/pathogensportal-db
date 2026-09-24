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


def _forecast_csv(path, rounds):
    rows = []
    for model, shift in (("ensemble", 0.0), ("baseline", 15.0)):      # baseline vedle o 15
        for kolo, first_week_end in rounds:
            for lead in range(4):
                end = pd.Timestamp(first_week_end) + pd.Timedelta(days=7 * lead)
                for q, v in ((0.025, 30), (0.25, 45), (0.5, 50), (0.75, 55), (0.975, 70)):
                    rows.append((model, kolo, "ILI", end.date().isoformat(), lead + 1, q, v + shift))
    pd.DataFrame(rows, columns=["model", "kolo", "ukazatel", "tyden_do", "horizont",
                                "kvantil", "hodnota"]).to_csv(path, index=False)


def test_forecast_block_pairs_round_with_last_observed_week(tmp_path, monkeypatch):
    (tmp_path / "ecdc").mkdir()
    # kolo ve středu 15. 1. 2025, první cílový týden končí v neděli 12. 1. (týden 2)
    _forecast_csv(tmp_path / "ecdc" / "respicast_cz.csv", [("2025-01-15", "2025-01-12")])
    monkeypatch.setattr(compute_mem, "DATA_DIR", tmp_path)
    series = _series([(2025, w, 50.0) for w in range(1, 6)])

    out = compute_mem.forecast_blocks("ili", series, threshold=50.0)

    assert list(out["by_last_observed_week"]) == ["2025-W01"]
    weeks = out["latest"]["weeks"]
    assert [w["week"] for w in weeks] == ["2025-W02", "2025-W03", "2025-W04", "2025-W05"]
    assert [w["lead"] for w in weeks] == [0, 1, 2, 3]                 # z dat, ne ze sloupce horizont
    assert weeks[0]["p_epidemic"] == 0.5 and weeks[0]["q975"] == 70.0
    ev = out["evaluation"]["overall"]
    assert ev["n"] == 4 and ev["coverage_95"] == 1.0 and ev["relative_wis"] < 1

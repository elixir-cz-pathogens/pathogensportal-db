"""Pozitivita chřipky a RSV (FluNet, ERVISS) — generátory nad malými vzorky dat.

Hlídá to, co se v těchhle řadách rozbije potichu: prázdné pole místo nuly,
poslední týdny bez výsledků, díru ve jmenovateli a procento z hrstky vzorků.
"""
import json

import numpy as np
import pandas as pd
import pytest

import generate_json as g

FLUNET_COLS = ["rok", "tyden", "tyden_od", "zdroj", "vysetreno", "inf_a", "inf_b", "inf_celkem",
               "a_h1n1pdm", "a_h3", "a_nesubtyp", "rsv"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(g, "FLUNET_FILE", tmp_path / "flunet.csv")
    monkeypatch.setattr(g, "ERVISS_LAB_FILE", tmp_path / "erviss.csv")
    return tmp_path


def _read(ws, name):
    return json.loads((ws / "out" / f"{name}.json").read_text(encoding="utf-8"))


def _flunet(ws, rows):
    """rows: (rok, tyden, zdroj, vysetreno, inf_a, inf_b, inf_celkem)"""
    full = [(r, t, "x", z, n, a, b, c, None, None, None, None) for r, t, z, n, a, b, c in rows]
    pd.DataFrame(full, columns=FLUNET_COLS).to_csv(ws / "flunet.csv", index=False)


def _erviss(ws, rows):
    """rows: (tyden_iso, typ, subtyp, ukazatel, hodnota) — věk total"""
    full = [(w, typ.split()[0], typ, sub, ind, "total", v) for w, typ, sub, ind, v in rows]
    pd.DataFrame(full, columns=["tyden_iso", "patogen", "typ", "subtyp", "ukazatel", "vek", "hodnota"]
                 ).to_csv(ws / "erviss.csv", index=False)


def _season(ws, name="flu_positivity_seasons"):
    out = _read(ws, name)
    return out, {d["label"]: dict(zip(out["labels"], d["data"])) for d in out["datasets"]}


# ── doplnění nul ─────────────────────────────────────────────────────────────

def test_empty_field_inside_series_is_zero_but_trailing_stays_empty():
    tests = pd.Series([100, 100, 100, 100, 100.0])
    got = g._detections_with_zeros(pd.Series([5, np.nan, 2, np.nan, np.nan]), tests)
    assert got.tolist()[:3] == [5, 0, 2]
    assert got.iloc[3:].isna().all()          # konec řady = ještě nenahlášeno, ne nula


def test_empty_field_without_tests_is_not_zero():
    got = g._detections_with_zeros(pd.Series([5, np.nan, 2.0]), pd.Series([100, np.nan, 100.0]))
    assert np.isnan(got.iloc[1])              # nevyšetřovalo se → nevíme nic


# ── sezónní srovnání (FluNet) ────────────────────────────────────────────────

def test_seasons_use_nonsentinel_only_and_split_at_week_40(workspace):
    _flunet(workspace, [
        (2023, 39, "NONSENTINEL", 200, 1, 0, 1),      # před sezónou — mimo osu
        (2023, 40, "NONSENTINEL", 200, 4, 0, 4),
        (2023, 40, "SENTINEL", 40, 20, 0, 20),        # jiný systém, nesmí se přičíst
        (2024, 5, "NONSENTINEL", 1000, 200, 50, 250),
        (2024, 40, "NONSENTINEL", 500, 5, 0, 5),      # už sezóna 2024/25
        (2025, 5, "NONSENTINEL", 1000, 100, 0, 100),
    ])
    g.flu_positivity_seasons()
    out, seasons = _season(workspace)

    assert out["labels"][0] == "40" and out["labels"][-1] == "20" and len(out["labels"]) == 33
    assert seasons["2023/24"]["40"] == 2.0 and seasons["2023/24"]["5"] == 25.0
    assert seasons["2024/25"]["40"] == 1.0 and seasons["2024/25"]["5"] == 10.0
    assert out["tests"]["2023/24"][out["labels"].index("5")] == 1000


def test_zero_weeks_reported_only_in_subtype_columns_count_as_zero(workspace):
    """Do 2025 nechával zdroj u nulového týdne inf_celkem prázdné a nuly psal do inf_a/inf_b."""
    _flunet(workspace, [
        (2023, 40, "NONSENTINEL", 300, 0, 0, None),
        (2023, 41, "NONSENTINEL", 300, 3, 0, 3),
    ])
    g.flu_positivity_seasons()
    assert _season(workspace)[1]["2023/24"]["40"] == 0.0


def test_seasons_before_the_denominator_change_are_left_out(workspace):
    _flunet(workspace, [
        (2019, 5, "NONSENTINEL", 40, 20, 0, 20),      # sezóna 2018/19: cílený výběr vzorků
        (2022, 5, "NONSENTINEL", 1000, 100, 0, 100),
    ])
    g.flu_positivity_seasons()
    assert list(_season(workspace)[1]) == ["2021/22"]


def test_too_few_specimens_give_no_percentage(workspace):
    _flunet(workspace, [
        (2023, 40, "NONSENTINEL", 12, 1, 0, 1),       # 8 % z dvanácti vzorků je šum
        (2023, 41, "NONSENTINEL", 300, 3, 0, 3),
    ])
    g.flu_positivity_seasons()
    out, seasons = _season(workspace)
    assert seasons["2023/24"]["40"] is None and seasons["2023/24"]["41"] == 1.0
    assert out["tests"]["2023/24"][0] == 12           # počet vzorků ale v datech zůstává


def test_week_53_is_pooled_with_week_52(workspace):
    _flunet(workspace, [
        (2026, 52, "NONSENTINEL", 1000, 100, 0, 100),
        (2026, 53, "NONSENTINEL", 1000, 300, 0, 300),
        (2027, 1, "NONSENTINEL", 1000, 50, 0, 50),
    ])
    g.flu_positivity_seasons()
    assert _season(workspace)[1]["2026/27"]["52"] == 20.0     # (100 + 300) / 2000


def test_at_most_six_seasons(workspace):
    _flunet(workspace, [(y, 5, "NONSENTINEL", 1000, 100, 0, 100) for y in range(2022, 2031)])
    g.flu_positivity_seasons()
    assert list(_season(workspace)[1]) == ["2024/25", "2025/26", "2026/27", "2027/28", "2028/29", "2029/30"]


def test_missing_flunet_file_is_skipped(workspace, capsys):
    g.flu_positivity_seasons()
    assert not (workspace / "out" / "flu_positivity_seasons.json").exists()
    assert "přeskakuji" in capsys.readouterr().out


# ── týdenní řada (ERVISS) ────────────────────────────────────────────────────

def _both(week, flu_tests, flu_det, rsv_tests, rsv_det):
    rows = []
    for typ, sub, tests, det in (("Influenza", "total", flu_tests, flu_det), ("RSV", "RSV", rsv_tests, rsv_det)):
        if tests is not None:
            rows.append((week, typ, sub, "tests", tests))
        if det is not None:
            rows.append((week, typ, sub, "detections", det))
    return rows


def test_weekly_starts_after_the_last_gap_in_a_denominator(workspace):
    rows = (_both("2024-W50", 1000, 50, 1000, 10)
            + _both("2024-W51", 1000, 80, None, 20)       # RSV bez počtu vyšetřených → díra
            + _both("2025-W01", 1000, 200, 1000, 50)
            + _both("2025-W02", 1000, 100, 500, 50))
    _erviss(workspace, rows)
    g.flu_positivity_weekly()
    out = _read(workspace, "flu_positivity_weekly")

    assert out["labels"] == ["2024-12-30", "2025-01-06"]     # pondělky ISO týdnů 1 a 2/2025
    assert out["first_week"] == "2025-W01" and out["last_week"] == "2025-W02"
    series = {d["label"]: d["data"] for d in out["datasets"]}
    assert series == {"Chřipka": [20.0, 10.0], "RSV": [5.0, 10.0]}   # každý virus vlastním jmenovatelem


def test_weekly_drops_the_tail_without_results_and_fills_inner_zeros(workspace):
    rows = (_both("2025-W20", 500, 5, 500, 5)
            + _both("2025-W21", 500, None, 500, None)     # uvnitř řady: nula
            + _both("2025-W22", 500, 10, 500, None)       # RSV ještě nemá výsledek
            + _both("2025-W23", 500, None, 500, None)     # konec: nenahlášeno
            + _both("2025-W24", 500, None, 500, None))
    _erviss(workspace, rows)
    g.flu_positivity_weekly()
    out = _read(workspace, "flu_positivity_weekly")

    assert out["last_week"] == "2025-W22" and len(out["labels"]) == 3
    series = {d["label"]: d["data"] for d in out["datasets"]}
    assert series["Chřipka"] == [1.0, 0.0, 2.0]
    assert series["RSV"] == [1.0, None, None]
    assert out["detections"]["Chřipka"] == [5, 0, 10]


def test_weekly_ignores_weeks_before_the_floor(workspace):
    """V týdnech 25–36/2022 ERVISS uvádí záchyty chřipky, které FluNet nepotvrzuje."""
    _erviss(workspace, _both("2022-W30", 160, 19, 160, 0) + _both("2022-W36", 150, 9, 150, 1))
    g.flu_positivity_weekly()
    assert not (workspace / "out" / "flu_positivity_weekly.json").exists()


def test_weekly_uses_total_rows_only(workspace):
    rows = _both("2025-W01", 1000, 100, 1000, 50)
    rows.append(("2025-W01", "Influenza A", "A(H3)", "detections", 60))     # subtyp se nesmí přičíst
    rows += _both("2025-W02", 1000, 100, 1000, 50)
    _erviss(workspace, rows)
    g.flu_positivity_weekly()
    series = {d["label"]: d["data"] for d in _read(workspace, "flu_positivity_weekly")["datasets"]}
    assert series["Chřipka"] == [10.0, 10.0]

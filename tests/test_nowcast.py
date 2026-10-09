"""
Nowcast — matematika na syntetickém trojúhelníku se známými faktory, žádný pohled
do budoucna, stínový log a zrcadlo snapshotů ERVISS (bez sítě).
"""

import gzip
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import compute_nowcast
import nowcast
from scrapers import ecdc_erviss_snapshots as snaps

FRI = date(2025, 1, 3)  # pátek — ERVISS snapshoty vycházejí v pátek


def synthetic(n_weeks=80, final=100, share=(0.75, 0.95), first_snap=FRI, distort_after=None):
    """Každý týden: v 1. snapshotu share[0]·final, ve 2. share[1]·final, pak final."""
    rows = []
    snapshots = [first_snap + timedelta(weeks=k) for k in range(n_weeks + nowcast.D_FINAL + 2)]
    for i in range(n_weeks):
        week = first_snap - timedelta(days=11) + timedelta(weeks=i)    # pondělí, d=0 v first_snap+i
        for s in snapshots:
            d = nowcast.delay_weeks(week, s)
            if d < 0:
                continue
            v = final * share[d] if d < len(share) else final
            if distort_after and s > distort_after:
                v *= 10
            rows.append({"week": week, "snapshot": s, "value": v})
    return pd.DataFrame(rows), snapshots


def test_delay_and_season():
    monday = date(2026, 9, 28)                     # KT 40/2026
    assert nowcast.delay_weeks(monday, date(2026, 10, 9)) == 0
    assert nowcast.delay_weeks(monday, date(2026, 10, 16)) == 1
    assert nowcast.delay_weeks(monday, date(2026, 10, 2)) < 0
    assert nowcast.in_season(monday)
    assert nowcast.in_season(date(2027, 5, 17))    # KT 20
    assert not nowcast.in_season(date(2027, 5, 24))  # KT 21


def test_factors_recovered():
    obs, snapshots = synthetic()
    model = nowcast.fit(nowcast.triangle(obs), snapshots[-1])
    assert model.factors[0] == pytest.approx(1 / 0.75)
    assert model.factors[1] == pytest.approx(1 / 0.95)


def test_no_look_ahead():
    origin = FRI + timedelta(weeks=60)
    clean, _ = synthetic()
    later, _ = synthetic(distort_after=origin)
    a = nowcast.fit(nowcast.triangle(clean), origin)
    b = nowcast.fit(nowcast.triangle(later), origin)
    assert a.factors == b.factors


def test_quantiles_bounded_by_reported():
    model = nowcast.Model(factors={0: 1.3, 1: 1.05}, dispersion={0: 5.0, 1: 5.0})
    rng = np.random.default_rng(0)
    q = nowcast.quantiles(200, model, 0, rng)
    assert (np.diff(q) >= 0).all() and q[0] >= 200
    assert q[3] == pytest.approx(260, rel=0.05)
    assert (nowcast.quantiles(0, model, 0, rng) == 0).all()


def test_backtest_beats_reported_on_systematic_delay():
    obs, _ = synthetic(n_weeks=120)
    res = nowcast.backtest(nowcast.triangle(obs))
    s = nowcast.summarize(res)
    assert s["n"] > 0 and s["improvement"] > 0.9


# ── driver: zrcadlo → JSON + stínový log ────────────────────────────────────

def _write_mirror(dirpath, obs_by_type):
    """obs_by_type: {pathogentype: DataFrame(week, snapshot, value)} → gz soubory jako scraper."""
    dirpath.mkdir(parents=True)
    rows = []
    for ptype, df in obs_by_type.items():
        for r in df.itertuples():
            y, w, _ = r.week.isocalendar()
            rows.append({"snapshot": r.snapshot, "countryname": "Czechia", "yearweek": f"{y}-W{w:02d}",
                         "pathogen": "RSV" if ptype == "RSV" else "Influenza", "pathogentype": ptype,
                         "pathogensubtype": "RSV" if ptype == "RSV" else "A(H3)",
                         "indicator": "detections", "age": "total", "value": r.value})
    allr = pd.DataFrame(rows)
    for s, g in allr.groupby("snapshot"):
        g.drop(columns="snapshot").to_csv(dirpath / f"{s}_nonSentinelTestsDetections.csv.gz",
                                         index=False, compression="gzip")


def test_compute_writes_shadow_json(tmp_path, monkeypatch):
    first = date(2025, 10, 3)
    obs, snapshots = synthetic(n_weeks=70, first_snap=first)
    _write_mirror(tmp_path / "ecdc" / "erviss_snapshots",
                  {"Influenza A": obs, "Influenza B": obs, "RSV": obs})
    as_of = date(2026, 10, 9)
    (tmp_path / "szu").mkdir()
    pd.DataFrame([
        {"sezona": "2026_2027", "rok": 2026, "tyden": 40, "virus": "Influenza A", "pocet": 30},
        {"sezona": "2026_2027", "rok": 2026, "tyden": 40, "virus": "RSV", "pocet": 10},
        {"sezona": "2025_2026", "rok": 2026, "tyden": 39, "virus": "Influenza A", "pocet": 20},
    ]).to_csv(tmp_path / "szu" / "szu_weekly_viry.csv", index=False)

    monkeypatch.setattr(compute_nowcast, "DATA_DIR", tmp_path)
    monkeypatch.setattr(compute_nowcast, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr("sys.argv", ["compute_nowcast.py", "--as-of", as_of.isoformat()])
    assert compute_nowcast.main() == 0

    out = json.loads((tmp_path / "out" / "flu_nowcast.json").read_text())
    assert out["status"] == "shadow" and out["display"] is False
    a = out["series"]["Influenza A (celkem)"]
    # KT 40 je v sezóně se zpožděním 0; KT 39 je mimo sezónu → bez odhadu
    assert [w["label"] for w in a["weeks"]] == ["KT 40/26"]
    assert a["weeks"][0]["reported"] == 30
    assert a["weeks"][0]["quantiles"]["0.5"] == pytest.approx(40, abs=3)
    assert out["series"]["Influenza B"]["weeks"][0]["reported"] == 0   # týden v matici je, B ne

    log = pd.read_csv(tmp_path / "nowcast" / "flu_nowcast_log.csv")
    assert len(log) == 3
    # druhý běh téhož dne log nezdvojí
    assert compute_nowcast.main() == 0
    assert len(pd.read_csv(tmp_path / "nowcast" / "flu_nowcast_log.csv")) == 3


def test_shadow_evaluation_scores_ripe_weeks():
    week = date(2026, 10, 5)
    log = pd.DataFrame([{"series": "RSV", "week": week.isoformat(), "d": 0, "as_of": "2026-10-16",
                         "reported": 10, **{f"q{lv}": v for lv, v in
                                            zip(nowcast.LEVELS, [10, 11, 12, 14, 16, 18, 22])}}])
    szu = pd.DataFrame([{"series": "RSV", "week": week, "reported": 15.0}])
    young = compute_nowcast.shadow_evaluation(log, szu, week + timedelta(weeks=4))
    assert young["RSV"] == {"n": 0, "pending": 1}
    ripe = compute_nowcast.shadow_evaluation(log, szu, week + timedelta(weeks=10))["RSV"]
    assert ripe["n"] == 1 and ripe["pending"] == 0
    assert ripe["coverage_95"] == 1.0 and ripe["wis_reported"] == 5.0
    assert ripe["wis_nowcast"] < ripe["wis_reported"]


# ── scraper zrcadla ─────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content, self._payload = content, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _csv(country):
    return (f"survtype,countryname,yearweek,pathogen,pathogentype,pathogensubtype,indicator,age,value\n"
            f"non-sentinel,{country},2026-W40,RSV,RSV,RSV,detections,total,3\n").encode()


def test_mirror_downloads_missing_cz_only(tmp_path, monkeypatch):
    tree = {"truncated": False, "tree": [
        {"path": "data/snapshots/2026-10-02_nonSentinelTestsDetections.csv"},
        {"path": "data/snapshots/2026-10-09_nonSentinelTestsDetections.csv"},
        {"path": "data/snapshots/2026-10-09_ILIARIRates.csv"},
    ]}
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "git/trees" in url:
            return _Resp(payload=tree)
        return _Resp(_csv("Czechia") + b"non-sentinel,Austria,2026-W40,RSV,RSV,RSV,detections,total,9\n")

    monkeypatch.setattr(snaps.requests, "get", fake_get)
    dest = tmp_path / "ecdc" / "erviss_snapshots"
    dest.mkdir(parents=True)
    (dest / "2026-10-02_nonSentinelTestsDetections.csv.gz").write_bytes(gzip.compress(_csv("Czechia")))

    assert snaps.download(tmp_path / "ecdc", today=date(2026, 10, 10)) == []
    assert sum("raw.githubusercontent" in c for c in calls) == 1       # jen chybějící
    got = pd.read_csv(dest / "2026-10-09_nonSentinelTestsDetections.csv.gz")
    assert set(got["countryname"]) == {"Czechia"}

    calls.clear()
    snaps.download(tmp_path / "ecdc", today=date(2026, 10, 12))      # čerstvé → žádné API
    assert calls == []


def test_mirror_rejects_missing_columns(monkeypatch):
    monkeypatch.setattr(snaps.requests, "get", lambda url, **kw: _Resp(b"countryname,value\nCzechia,1\n"))
    with pytest.raises(ValueError, match="chybí sloupce"):
        snaps.fetch_cz("2026-10-09")

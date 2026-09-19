"""Kanál hlášení EWS — verdikt o signálu, když část případů přišla novou cestou."""
import numpy as np
import pandas as pd

import detect_anomalies as da


def _res(observed, threshold, signal=True, kind="glm"):
    return {"type": kind, "observed": observed, "threshold": threshold, "signal": signal}


def test_signal_survives_when_even_non_ews_cases_exceed_threshold():
    # hepatitida A, Jihomoravský kraj 12/2025: 80 nahlášeno, 1 přes EWS, práh 5
    v = da.channel_verdict(_res(80.0, 5.04), ews=1.0)
    assert v["robust"] and v["lower_bound"] == 79.0 and v["share"] == 0.01


def test_signal_is_undecided_when_new_channel_alone_could_explain_it():
    # mononukleóza, Praha 12/2025: 39 nahlášeno, 33 přes EWS, práh 26,9
    v = da.channel_verdict(_res(39.0, 26.87), ews=33.0)
    assert not v["robust"] and v["lower_bound"] == 6.0


def test_untouched_signals_and_non_signals_carry_no_verdict():
    assert da.channel_verdict(_res(80.0, 5.0), ews=0.0) is None
    assert da.channel_verdict(_res(3.0, 5.0, signal=False), ews=2.0) is None


def test_rare_disease_rule_uses_cluster_size_not_threshold():
    # pravidlo „shluk od 2 případů“: 3 nahlášené, 2 přes EWS → zbyde 1, shluk to není
    assert not da.channel_verdict(_res(3.0, da.RARE_ALERT - 0.5, kind="rare"), ews=2.0)["robust"]
    assert da.channel_verdict(_res(3.0, da.RARE_ALERT - 0.5, kind="rare"), ews=1.0)["robust"]


def test_ews_grid_is_aligned_with_counts_and_national_total(tmp_path):
    rows = [("A", "Nemoc", "CZ010", "Praha", 2025, 6, 10, 0), ("A", "Nemoc", "CZ010", "Praha", 2025, 7, 30, 20),
            ("A", "Nemoc", "CZ020", "Střed", 2025, 7, 8, 5)]
    path = tmp_path / "isin.csv"
    pd.DataFrame(rows, columns=["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev",
                                "rok", "mesic", "pocet_pripadu", "EWS"]).to_csv(path, index=False)
    long, _, n = da.load_series(path)
    grid = da.ews_grid(long, n)
    assert list(grid[("A", "CZ010")]) == [0.0, 20.0]
    assert list(grid[("A", "CZ")]) == [0.0, 25.0]            # celostátní řada sčítá i EWS


def test_snapshot_without_ews_column_still_loads(tmp_path):
    path = tmp_path / "old.csv"
    pd.DataFrame([("A", "Nemoc", "CZ010", "Praha", 2024, 1, 4)],
                 columns=["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev", "rok", "mesic",
                          "pocet_pripadu"]).to_csv(path, index=False)
    long, _, n = da.load_series(path)
    assert da.ews_grid(long, n)[("A", "CZ010")].sum() == 0

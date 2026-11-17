"""Týdny → měsíce (scripts/weekly_months.py) a týdenní zdroj detektoru signálů.

Měsíc přepočtený z týdnů je odhad. Testy hlídají to, co se u odhadu pokazit
nesmí: žádný případ se neztratí ani nezdvojí, EWS nikdy nepřevýší celkový počet,
neúplný měsíc se nehodnotí — a že se u diagnóz s novým kanálem hlášení měsíce
s EWS nedostanou do baseline.
"""
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import detect_anomalies as da
import weekly_months as wm


# ── kalendář ─────────────────────────────────────────────────────────────────

def test_easter_holidays_move_with_the_year():
    assert {date(2026, 4, 3), date(2026, 4, 6)} <= wm.public_holidays(2026)   # Velký pátek, pondělí
    assert {date(2025, 4, 18), date(2025, 4, 21)} <= wm.public_holidays(2025)
    assert len(wm.public_holidays(2026)) == 13


def test_week_inside_one_month_is_not_split():
    assert wm.week_shares(2026, 42) == [(2026, 10, 1.0)]


def test_week_across_months_is_split_by_working_days_without_holidays():
    # 28. 9. – 4. 10. 2026: pondělí 28. 9. je svátek → 2 pracovní dny v září, 2 v říjnu
    assert wm.week_shares(2026, 40) == [(2026, 9, 0.5), (2026, 10, 0.5)]
    # 29. 12. 2025 – 4. 1. 2026: tři pracovní dny v prosinci, 1. 1. svátek, pátek 2. 1.
    assert wm.week_shares(2026, 1) == [(2025, 12, 0.75), (2026, 1, 0.25)]


def test_shares_of_every_week_sum_to_one():
    for year in range(2022, 2028):
        for week in range(1, date(year, 12, 28).isocalendar().week + 1):
            assert sum(s for _, _, s in wm.week_shares(year, week)) == pytest.approx(1.0)


# ── dělení celých případů ────────────────────────────────────────────────────

def test_split_keeps_whole_cases_and_totals():
    assert wm.split_count(10, 4, 0.6) == (6, 2, 4, 2)
    assert wm.split_count(1, 1, 0.5) == (1, 1, 0, 0)       # polovina jde do prvního měsíce
    assert wm.split_count(2, 0, 0.75) == (2, 0, 0, 0)      # 1,5 → 2, druhý měsíc nedostane nic
    assert wm.split_count(0, 0, 0.4) == (0, 0, 0, 0)


def test_split_never_gives_a_month_more_ews_than_cases():
    rng = np.random.default_rng(1)
    for _ in range(2000):
        n = int(rng.integers(0, 60))
        ews = int(rng.integers(0, n + 1))
        share = float(rng.choice([0.2, 0.25, 1 / 3, 0.4, 0.5, 0.6, 2 / 3, 0.75, 0.8]))
        n1, e1, n2, e2 = wm.split_count(n, ews, share)
        assert n1 + n2 == n and e1 + e2 == ews
        assert 0 <= e1 <= n1 and 0 <= e2 <= n2


# ── úplné měsíce ─────────────────────────────────────────────────────────────

def test_month_counts_as_complete_by_working_days():
    # data od pondělí 3. 1. 2022: 1. a 2. ledna byl víkend → leden 2022 je celý
    months = wm.complete_months(date(2022, 1, 3), date(2026, 11, 14))
    assert months[0] == (2022, 1) and months[-1] == (2026, 10)
    # říjen 2026 končí pracovním pátkem 30. 10.; den předtím ještě celý není
    assert wm.complete_months(date(2022, 1, 3), date(2026, 10, 29))[-1] == (2026, 9)
    assert wm.complete_months(date(2022, 1, 3), date(2026, 10, 30))[-1] == (2026, 10)
    # data začínající uprostřed měsíce ho nedají celý
    assert wm.complete_months(date(2022, 1, 10), date(2022, 3, 31))[0] == (2022, 2)


def _weekly(first: date, last: date, rows) -> pd.DataFrame:
    """rows(monday) → [(diagnoza, kraj, pocet, ews), …] pro každý týden od first do last."""
    out = []
    monday = first
    while monday <= last:
        iso = monday.isocalendar()
        for dg, kraj, n, ews in rows(monday):
            out.append((dg, kraj, iso.year, iso.week, "Celkem", n, ews))
            out.append((dg, kraj, iso.year, iso.week, "0 let", n, ews))      # věk se nesmí přičíst
        monday += timedelta(days=7)
    return pd.DataFrame(out, columns=["diagnoza", "kraj_kod", "rok", "tyden", "vek", "pocet", "pocet_ews"])


def test_to_months_loses_no_case_and_drops_the_open_month():
    weekly = _weekly(date(2026, 8, 31), date(2026, 11, 9), lambda m: [("A02", "CZ", 7, 2)])
    monthly, months = wm.to_months(weekly, date(2026, 11, 14))
    assert months == [(2026, 9), (2026, 10)]                 # srpen i listopad jsou neúplné
    got = monthly.set_index(["rok", "mesic"])
    # září: týden 36 začíná pondělím 31. 8., čtyři z pěti pracovních dnů jsou zářijové (6 ze 7),
    # týdny 37–39 celé (21) a polovina týdne 40 (4 ze 7)
    assert got.loc[(2026, 9), "pocet_pripadu"] == 6 + 21 + 4
    # říjen: druhá polovina týdne 40 (3) a týdny 41–44; týden 44 má všechny pracovní dny v říjnu
    assert got.loc[(2026, 10), "pocet_pripadu"] == 3 + 28
    assert (monthly["ews"] <= monthly["pocet_pripadu"]).all()


# ── týdenní zdroj detektoru ──────────────────────────────────────────────────

def _state(tmp_path, stav_k="2026-11-15T05:34", codes=("A02", "B02")):
    names = {"A02": {"cs": "SALMONELÓZA", "en": "Other infections caused by salmonella"},
             "B02": {"cs": "PÁSOVÝ OPAR", "en": "Shingles [herpes zoster]"},
             "B01": {"cs": "PLANÉ NEŠTOVICE", "en": "Chickenpox [varicella]"},
             "X99": {"cs": "NEZNÁMÁ NEMOC", "en": "Unknown disease"}}
    path = tmp_path / "isin_tydenni_stav.json"
    path.write_text(json.dumps({"slozka": "2026-11-16", "stav_k": stav_k,
                                "diagnozy": {c: names[c] for c in codes}}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _source(tmp_path, rows, codes=("A02", "B02")):
    csv = tmp_path / "isin_tydenni.csv"
    _weekly(date(2022, 1, 3), date(2026, 11, 9), rows).to_csv(csv, index=False)
    return csv, _state(tmp_path, codes=codes)


def test_weekly_series_have_the_shape_the_detector_expects(tmp_path):
    csv, state = _source(tmp_path, lambda m: [("A02", "CZ", 10, 0), ("A02", "CZ010", 3, 0),
                                              ("X99", "CZ", 1, 0)], codes=("A02", "X99"))
    long, periods, n, info = da.load_weekly_series(csv, state)
    assert periods[0] == "2022-01" and periods[-1] == "2026-10" and n == len(periods) == 58
    assert info["data_until"] == "2026-11-14"
    # celá ČR je list ČR ze zdroje, ne součet krajů (část záznamů kraj nemá)
    oct_cz = long[(long.kraj_kod == "CZ") & (long.diagnoza == "A02") & (long.t == n - 1)]
    assert oct_cz.pocet_pripadu.item() > 3 * 4
    # čtenářský název z curated/diagnosis_names.yaml; bez něj název zdroje malými písmeny
    assert set(long[long.diagnoza == "A02"].diagnoza_nazev) == {"Salmonelóza"}
    assert set(long[long.diagnoza == "X99"].diagnoza_nazev) == {"Neznámá nemoc"}
    assert info["diagnozy"]["A02"]["official"]["cs"] == "SALMONELÓZA"
    assert set(long[long.kraj_kod == "CZ010"].kraj_nazev) == {"Hl. m. Praha"}


def test_unknown_region_is_an_error_not_a_silent_series(tmp_path):
    csv, state = _source(tmp_path, lambda m: [("A02", "CZ", 5, 0), ("A02", "CZ123", 5, 0)], codes=("A02",))
    with pytest.raises(ValueError, match="neznámé území"):
        da.load_weekly_series(csv, state)


def _ews_rows(monday):
    """
    A02 bez EWS; B02 od července 2025 z 80 % přes EWS a na trojnásobku (nový kanál);
    B01 od července 2025 z 80 % přes EWS, ale na stejné úrovni (přesun).
    """
    if monday >= date(2025, 6, 30):
        return [("A02", "CZ", 20, 1), ("B02", "CZ", 60, 48), ("B01", "CZ", 20, 16)]
    return [("A02", "CZ", 20, 0), ("B02", "CZ", 20, 0), ("B01", "CZ", 20, 0)]


CODES = ("A02", "B01", "B02")


def test_diagnoses_are_grouped_by_what_ews_did_to_them(tmp_path):
    long, periods, n, _ = da.load_weekly_series(*_source(tmp_path, _ews_rows, codes=CODES))
    start = periods.index("2025-07")
    cls = da.classify_weekly(long, periods, n - 1)
    assert cls["A02"]["group"] == "bez_zmeny"                    # 5 % z EWS
    assert cls["B01"]["group"] == "presun" and cls["B01"]["level_ratio"] == pytest.approx(1.0, abs=0.1)
    assert cls["B02"]["group"] == "novy_kanal" and cls["B02"]["level_ratio"] > 2.5
    # přesun nechává měsíce s EWS v baseline (jsou srovnatelné), nový kanál ne
    assert not cls["B01"]["exclude"]
    assert cls["B02"]["exclude"] == frozenset(range(start, n))
    # obvyklé rozpětí je z let před EWS (20 týdně ≈ 87–91 měsíčně), ne z trojnásobku po něm
    assert cls["B02"]["range"][(n - 1) % 12][1] < 100
    # měsíc před zavedením EWS se hodnotí postaru
    assert all(c["group"] == "bez_zmeny" for c in da.classify_weekly(long, periods, start - 1).values())


def test_epidemic_months_leave_the_baseline(tmp_path):
    def rows(monday):
        n = 200 if date(2024, 3, 4) <= monday <= date(2024, 8, 26) else 10    # epidemie jaro–léto 2024
        return [("A02", "CZ", n, 0)]
    long, periods, n, _ = da.load_weekly_series(*_source(tmp_path, rows, codes=("A02",)))
    cls = da.classify_weekly(long, periods, n - 1)["A02"]
    assert cls["epidemic"][0] == "2024-03" and "2024-08" in cls["epidemic"]
    assert {periods.index(p) for p in cls["epidemic"]} <= cls["exclude"]
    assert cls["range"][periods.index("2024-05") % 12][1] < 100    # epidemie není „obvyklé rozpětí“


@pytest.fixture
def weekly_run(tmp_path, monkeypatch):
    (tmp_path / "isin").mkdir()
    monkeypatch.setattr(da, "DATA_DIR", tmp_path)
    monkeypatch.setattr(da, "CHARTS_OUT", tmp_path / "out")
    monkeypatch.setattr("sys.argv", ["detect_anomalies.py", "--zdroj", "tydenni"])
    return tmp_path


def test_weekly_run_writes_its_own_file_with_all_diagnoses(weekly_run):
    def rows(monday):
        out = _ews_rows(monday)
        if date(2026, 10, 5) <= monday <= date(2026, 10, 26):
            out[0] = ("A02", "CZ", 90, 4)                # říjen 2026: salmonelóza na 4,5násobku
            out[2] = ("B01", "CZ", 90, 72)               # … a neštovice taky, z 80 % přes EWS
        return out
    _source(weekly_run / "isin", rows, codes=CODES)
    assert da.main() == 0
    out = json.loads((weekly_run / "out" / "anomaly_signals_weekly.json").read_text(encoding="utf-8"))
    assert not (weekly_run / "out" / "anomaly_signals.json").exists()   # hlavní výstup se nepřepisuje

    assert out["target_period"] == "2026-10"
    assert out["input"]["source"] == "uzis-isin-weekly" and out["input"]["history_from"] == "2022-01"
    status = {r["diagnoza"]: r for r in out["national"]}
    assert status["A02"]["status"] == "signal" and status["A02"]["group"] == "bez_zmeny"
    # přesun: signál se posuzuje na celkovém počtu, EWS ho nezpochybní
    assert status["B01"]["status"] == "signal" and status["B01"]["group"] == "presun"
    # nový kanál: trojnásobek je změna hlášení, ne signál — a řada se neskóruje
    assert status["B02"]["status"] == "zmena_hlaseni" and "threshold" not in status["B02"]
    assert {s["diagnoza"] for s in out["signals"]} == {"A02", "B01"}
    assert "reporting_channel" not in next(s for s in out["signals"] if s["diagnoza"] == "B01")
    # graf: posledních 12 měsíců s obvyklým rozpětím
    m = status["B02"]["months"]
    assert len(m) == 12 and m[-1]["period"] == "2026-10" and m[-1]["n"] > m[-1]["hi"]
    assert out["signals"][0]["diagnoza_nazev_en"] in ("Salmonellosis", "Chickenpox")
    assert all("ews_observed" not in s for s in out["signals"])


def test_weekly_run_without_the_source_writes_nothing_and_does_not_fail(weekly_run):
    assert da.main() == 0
    assert not (weekly_run / "out").exists()


def test_weekly_run_refuses_as_of(weekly_run, monkeypatch):
    monkeypatch.setattr("sys.argv", ["detect_anomalies.py", "--zdroj", "tydenni", "--as-of", "2026-11-01"])
    assert da.main() == 2

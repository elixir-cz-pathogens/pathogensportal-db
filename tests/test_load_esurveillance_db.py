"""
Denní e-surveillance v PostgreSQL: které snímky v databázi zůstávají.

Běží jen na vyžádání, stejně jako test_load_pathogens_db.py (PATHOGENS_TEST_DSN);
každý test má vlastní schéma, které po sobě smaže.
"""
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

import load_to_db

DSN = os.environ.get("PATHOGENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PATHOGENS_TEST_DSN není nastavené")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path TO {schema}")
        c.execute((ROOT / "db" / "init.sql").read_text(encoding="utf-8"))
        try:
            yield c
        finally:
            c.execute(f"DROP SCHEMA {schema} CASCADE")


def _rows(snap: date, value: float) -> list:
    """Dva dny celostátních hospitalizací s chřipkou pod daným snímkem."""
    return [("uzis_esurv", None, "Chřipka", None, None, None, d, d + timedelta(days=1), "hospitalized", value, snap)
            for d in (date(2026, 11, 20), date(2026, 11, 21))]


def _load(conn, monkeypatch, snap: date, value: float):
    monkeypatch.setattr(load_to_db, "esurveillance_rows", lambda snapshot=None: (_rows(snap, value), snap))
    load_to_db.load_esurveillance(conn)


def _snapshots(conn) -> list:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM observation WHERE source_id = 'uzis_esurv' ORDER BY 1")]


def test_zustava_posledni_snimek_a_pondelni(conn, monkeypatch):
    # 19.–26. 11. 2026: pondělí je 23. 11.
    for day in range(19, 27):
        _load(conn, monkeypatch, date(2026, 11, day), float(day))
    assert _snapshots(conn) == [date(2026, 11, 23), date(2026, 11, 26)]
    # aktuální stav = nejnovější snímek
    assert conn.execute("""
        SELECT value FROM observation WHERE source_id = 'uzis_esurv'
           AND snapshot_date = (SELECT max(snapshot_date) FROM observation WHERE source_id = 'uzis_esurv')
    """).fetchall() == [(26,), (26,)]


def test_opakovane_nahrani_tehoz_snimku_jen_prepise(conn, monkeypatch):
    _load(conn, monkeypatch, date(2026, 11, 25), 60.0)
    _load(conn, monkeypatch, date(2026, 11, 25), 61.0)        # zdroj den opravil pod stejnou složkou
    assert conn.execute("SELECT count(*), max(value) FROM observation").fetchone() == (2, 61)


def test_jine_zdroje_se_mazani_netyka(conn, monkeypatch):
    conn.execute("""
        INSERT INTO observation (source_id, diagnosis_name, period_start, period_end, metric, value, snapshot_date)
        VALUES ('isin', 'Spalničky', '2026-01-01', '2026-02-01', 'cases', 7, '2026-11-19'),
               ('isin', 'Spalničky', '2026-01-01', '2026-02-01', 'cases', 8, '2026-11-20')
    """)
    for day in (24, 25, 26):
        _load(conn, monkeypatch, date(2026, 11, day), 1.0)
    assert conn.execute("SELECT count(*) FROM observation WHERE source_id = 'isin'").fetchone()[0] == 2
    assert _snapshots(conn) == [date(2026, 11, 26)]

"""
Loader registru patogenů proti skutečnému PostgreSQL.

Běží jen na vyžádání — sada jinak nepotřebuje databázi a v CI žádná není:

    docker run -d --rm -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:16-alpine
    PATHOGENS_TEST_DSN="host=127.0.0.1 port=55432 user=postgres password=test dbname=postgres" \
        pytest -q tests/test_load_pathogens_db.py

Každý test dostane vlastní schéma, které po sobě smaže; tabulek v `public`
(ani ostrých dat, kdyby DSN omylem mířilo na ně) se nedotkne.
"""
import copy
import json
import os
import uuid
from datetime import date
from pathlib import Path

import pytest

import load_pathogens
import pathogen_registry as registry

DSN = os.environ.get("PATHOGENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PATHOGENS_TEST_DSN není nastavené")

ROOT = Path(__file__).resolve().parent.parent
# Schéma z doby před registrem — tabulka `pathogens` se čtyřmi řádky a nic dalšího.
OLD_SCHEMA = """
CREATE TABLE pathogens (
    id SERIAL PRIMARY KEY, slug VARCHAR(255) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    scientific_name VARCHAR(255),
    category VARCHAR(100) CHECK (category IN ('virus', 'bacteria', 'fungus', 'parasite', 'other')),
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
INSERT INTO pathogens (slug, name, scientific_name, category) VALUES
    ('influenza-a', 'Influenza A', 'Influenza A virus', 'virus'),
    ('zruseny', 'Zrušený záznam', NULL, 'other');
"""


@pytest.fixture
def conn():
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path TO {schema}")
        try:
            yield c
        finally:
            c.execute(f"DROP SCHEMA {schema} CASCADE")


def _schema(conn):
    conn.execute((ROOT / "db" / "init.sql").read_text(encoding="utf-8"))


def _one(conn, query, *params):
    return conn.execute(query, params).fetchone()


@pytest.fixture
def reg():
    return registry.load()


def test_stara_databaze_se_rozsiri_a_naplni(conn, reg, tmp_path):
    conn.execute(OLD_SCHEMA)
    _schema(conn)                       # ADD COLUMN IF NOT EXISTS na existující tabulce
    result = load_pathogens.load(conn, reg, registry.vocabulary(reg), tmp_path)

    assert "influenza-a" in result["updated"]       # starý řádek dostal data z registru
    assert result["deleted"] == ["zruseny"]         # co v registru není, zmizí
    assert _one(conn, "SELECT count(*) FROM pathogens")[0] == len(reg["pathogens"])
    row = _one(conn, "SELECT name, name_cs, taxon_id, verified_on FROM pathogens WHERE slug = 'influenza-a'")
    assert row[:3] == ("Influenza A virus", "Virus chřipky A", 11320) and isinstance(row[3], date)


def test_druhy_beh_nic_nezmeni_a_updated_at_zustane(conn, reg, tmp_path):
    _schema(conn)
    voc = registry.vocabulary(reg)
    load_pathogens.load(conn, reg, voc, tmp_path)
    before = conn.execute("SELECT slug, updated_at FROM pathogens ORDER BY slug").fetchall()

    result = load_pathogens.load(conn, reg, voc, tmp_path)
    assert result["inserted"] == result["updated"] == result["deleted"] == []
    assert result["unchanged"] == len(reg["pathogens"])
    assert conn.execute("SELECT slug, updated_at FROM pathogens ORDER BY slug").fetchall() == before


def test_zmena_zaznamu_prepise_jen_ten_zaznam(conn, reg, tmp_path):
    _schema(conn)
    voc = registry.vocabulary(reg)
    load_pathogens.load(conn, reg, voc, tmp_path)
    stamps = dict(conn.execute("SELECT slug, updated_at FROM pathogens").fetchall())

    changed = copy.deepcopy(reg)
    tbe = next(p for p in changed["pathogens"] if p["id"] == "tbe")
    tbe["synonyms"] = {"cs": ["Klíšťovka"]}
    tbe["dashboards"] = ["/dashboards/genomics/"]
    changed["pathogens"] = [p for p in changed["pathogens"] if p["id"] != "yellow-fever"]
    conn.execute("SELECT pg_sleep(0.01)")
    result = load_pathogens.load(conn, changed, voc, tmp_path)

    assert result["updated"] == ["tbe"] and result["deleted"] == ["yellow-fever"]
    after = dict(conn.execute("SELECT slug, updated_at FROM pathogens").fetchall())
    assert after["tbe"] > stamps["tbe"]
    assert all(after[s] == stamps[s] for s in after if s != "tbe")
    assert _one(conn, "SELECT dashboards FROM pathogen_overview WHERE slug = 'tbe'")[0] == ["/dashboards/genomics/"]
    assert _one(conn, "SELECT count(*) FROM pathogen_glossary WHERE slug = 'tbe' AND term = 'Klíšťovka'")[0] == 1
    # vazby smazaného patogenu odešly s ním
    assert _one(conn, "SELECT count(*) FROM pathogen_term t LEFT JOIN pathogens p ON p.id = t.pathogen_id "
                      "WHERE p.id IS NULL")[0] == 0


def test_pohledy_davaji_identifikatory_slovnicek_a_cerstvost(conn, reg, tmp_path):
    (tmp_path / "flu_weekly.json").write_text(json.dumps({
        "meta": {"generated_at": "2026-11-16T20:54:25Z", "period": {"end_date": "2026-11-08"}}}))
    (tmp_path / "genomics_overview.json").write_text(json.dumps({"generated_at": "2026-11-20T01:00:00Z"}))
    _schema(conn)
    load_pathogens.load(conn, reg, registry.vocabulary(reg), tmp_path)

    uri = _one(conn, "SELECT uri FROM pathogen_ids WHERE slug = 'influenza-a' AND scheme = 'ena-taxon'")[0]
    assert uri == "https://www.ebi.ac.uk/ena/browser/view/Taxon:11320"

    # úřední název diagnózy jen u kódu, který patří výhradně tomuto patogenu
    terms = {(lang, kind, term) for lang, kind, term in conn.execute(
        "SELECT lang, kind, term FROM pathogen_glossary WHERE slug = 'measles'")}
    assert ("cs", "diagnosis", "Spalničky") in terms and ("en", "diagnosis", "Measles") in terms
    assert _one(conn, "SELECT count(*) FROM pathogen_glossary WHERE slug = 'campylobacter' AND code = 'A04'")[0] == 0

    through, generated, pages = _one(
        conn, "SELECT data_through, data_generated_at, dashboards FROM pathogen_overview WHERE slug = 'influenza-a'")
    assert through == date(2026, 11, 8)
    assert generated.isoformat().startswith("2026-11-20")       # novější z jeho stránek
    assert "/dashboards/influenza-surveillance/" in pages
    # patogen bez stránek s daty: prázdno, ne chyba
    assert _one(conn, "SELECT data_through FROM pathogen_overview WHERE slug = 'andes-virus'")[0] is None


def test_registr_jde_spojit_s_pozorovanimi(conn, reg, tmp_path):
    """Smysl kódu MKN-10 v registru: `diagnosis.code` je `observation.diagnosis_code`."""
    _schema(conn)
    load_pathogens.load(conn, reg, registry.vocabulary(reg), tmp_path)
    conn.execute("""
        INSERT INTO observation (source_id, diagnosis_code, diagnosis_name, period_start, period_end,
                                 metric, value, snapshot_date)
        VALUES ('isin', 'B05', 'Spalničky', '2026-01-01', '2026-02-01', 'cases', 7, '2026-11-26'),
               ('isin', 'A04', 'Jiné bakteriální střevní infekce', '2026-01-01', '2026-02-01', 'cases', 900, '2026-11-26')
    """)
    rows = conn.execute("""
        SELECT p.slug, sum(o.value)
          FROM observation o
          JOIN pathogen_diagnosis d ON d.code = o.diagnosis_code AND NOT d.shared
          JOIN pathogens p ON p.id = d.pathogen_id
         GROUP BY p.slug
    """).fetchall()
    assert rows == [("measles", 7)]      # A04 je sdílený kód — nikomu se nepřipíše


def test_selhani_registru_nezahodi_ostatni_zapisy(conn, monkeypatch):
    """Krok běží ve vlastním savepointu: po jeho pádu jde ve stejné transakci psát dál."""
    import load_to_db

    _schema(conn)

    def boom(c):
        c.execute("INSERT INTO pathogens (slug, name) VALUES ('rozpracovany', 'x')")
        c.execute("SELECT * FROM tabulka_ktera_neexistuje")

    monkeypatch.setattr(load_pathogens, "run", boom)
    with conn.transaction():
        conn.execute("INSERT INTO population (region_code, year, value) VALUES ('CZ', 2025, 1)")
        assert load_to_db.load_pathogen_registry(conn) is False
        conn.execute("INSERT INTO population (region_code, year, value) VALUES ('CZ', 2026, 2)")
    assert _one(conn, "SELECT count(*) FROM population")[0] == 2
    assert _one(conn, "SELECT count(*) FROM pathogens WHERE slug = 'rozpracovany'")[0] == 0

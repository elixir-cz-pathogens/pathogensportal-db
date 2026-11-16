"""Kódy MKN-10 u řad grafů ISIN — identifikátor řady vedle jejího českého jména."""
import json

import pandas as pd
import pytest

import generate_json as g

COLUMNS = ["rok", "mesic", "kraj_kod", "kraj_nazev", "diagnoza", "diagnoza_nazev",
           "vek_kod", "vek_nazev", "pohlavi", "EWS", "pocet_pripadu"]


def _row(year, month, code, name, n):
    return (year, month, "CZ010", "Hl. m. Praha", code, name, "66015019", "15–19 let", "M", 0, n)


@pytest.fixture
def isin(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(g, "ISIN_FILE", tmp_path / "isin.csv")

    def write(rows):
        pd.DataFrame(rows, columns=COLUMNS).to_csv(tmp_path / "isin.csv", index=False)

    def read(name):
        return json.loads((tmp_path / "out" / f"{name}.json").read_text(encoding="utf-8"))

    return write, read


def test_rady_nesou_kod_diagnozy(isin):
    write, read = isin
    write([_row(2024, 1, "B01", "Plané neštovice [varicella]", 50),
           _row(2025, 2, "B01", "Plané neštovice [varicella]", 40),
           _row(2024, 1, "A37", "Dávivý kašel [pertussis]", 30)])
    g.isin_top_diseases()
    g.isin_monthly_trend()
    g.isin_disease_groups()

    for name in ("isin_top_diseases", "isin_monthly_trend", "isin_group_childhood_airborne"):
        chart = read(name)
        codes = {d["label"]: d.get("code") for d in chart["datasets"]}
        assert codes["Plané neštovice [varicella]"] == "B01", name
        assert chart["meta"]["series_code_system"]["id"] == "ICD-10", name
    top = {d["label"]: d["code"] for d in read("isin_top_diseases")["datasets"]}
    assert top["Dávivý kašel [pertussis]"] == "A37"


def test_souhrnna_rada_kod_nema(isin):
    """„Ostatní diagnózy (souhrn)“ je součet desítek kódů — žádný jeden mu nepatří."""
    write, read = isin
    write([_row(2024, 1, "A99", "Nezařazená diagnóza pro test", 7)])
    g.isin_disease_groups()
    other = read("isin_group_other")
    assert "code" not in other["datasets"][0]
    assert "series_code_system" not in other["meta"]


def test_nazev_s_vice_kody_zustane_bez_kodu():
    """Řada bez kódu je lepší než řada se špatným."""
    df = pd.DataFrame([_row(2024, 1, "A01", "Dvojznačná", 1), _row(2024, 1, "A02", "Dvojznačná", 1),
                       _row(2024, 1, "A03", "Shigelóza", 1)], columns=COLUMNS)
    assert g._isin_codes(df) == {"Shigelóza": "A03"}


def test_stary_vstup_bez_sloupce_kodu_graf_neshodi():
    df = pd.DataFrame({"diagnoza_nazev": ["Shigelóza"], "pocet_pripadu": [1]})
    assert g._isin_codes(df) == {}

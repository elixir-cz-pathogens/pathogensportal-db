"""Registry ÚZIS mimo ISIN — generátory grafů nad malými vzorky dat."""
import json

import pandas as pd
import pytest

import generate_json as g


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(g, "STI_FILE", tmp_path / "sti.csv")
    monkeypatch.setattr(g, "TBC_FILE", tmp_path / "tbc.csv")
    monkeypatch.setattr(g, "POPULATION_FILE", tmp_path / "pop.csv")
    pop = [(y, k, n, s, c) for y in (2023, 2024)
           for k, n, base in (("CZ", "Česko", 10_000_000), ("CZ010", "Praha", 1_000_000), ("CZ064", "JMK", 1_200_000))
           for s, c in (("Celkem", base), ("Muži", base * 0.49), ("Ženy", base * 0.51))]
    pd.DataFrame(pop, columns=["rok", "kraj_kod", "kraj_nazev", "pohlavi", "pocet"]).to_csv(tmp_path / "pop.csv", index=False)
    return tmp_path


def _read(ws, name):
    return json.loads((ws / "out" / f"{name}.json").read_text(encoding="utf-8"))


def test_sti_sums_syphilis_stages_and_maps_district_to_region(workspace):
    rows = [(2024, "CZ0100", "M", "25–34 let", "A51", "Časná syfilis", 30),
            (2024, "CZ0100", "M", "25–34 let", "A52", "Pozdní syfilis", 10),
            (2024, "CZ0642", "Z", "19–24 let", "A54", "Gonokoková infekce", 60),
            (2024, "CZ0999", "M", "19–24 let", "A54", "Gonokoková infekce", 5)]      # bez okresu v ČR
    pd.DataFrame(rows, columns=["rok", "okres_kod", "pohlavi", "vek_nazev", "diagnoza_kod",
                                "diagnoza_nazev", "pocet_pripadu"]).to_csv(workspace / "sti.csv", index=False)
    g.sti_registry()

    trend = {d["label"]: d["data"] for d in _read(workspace, "sti_registry_trend")["datasets"]}
    assert trend["Syfilis"] == [40] and trend["Kapavka"] == [65]                    # stadia sečtená
    assert _read(workspace, "sti_registry_incidence")["datasets"][1]["data"] == [0.65]
    regions = _read(workspace, "sti_registry_map")["regions"]
    assert regions == {"CZ010": 4.0, "CZ064": 5.0}                                   # CZ099 do mapy nejde
    sex = {d["label"]: d["data"][0] for d in _read(workspace, "sti_registry_sex")["datasets"]}
    assert sex["Muži"] == pytest.approx(45 / 4_900_000 * 1e5, abs=0.01)             # vlastní jmenovatel


def test_tbc_map_averages_years_and_splits_by_birth_country(workspace):
    rows = [(y, y, 1, "CZ0100", "CZ010", "Eastern Europe", cz, "19–64 let", "x", n)
            for y, cz, n in ((2022, 1, 30), (2023, 1, 30), (2024, 1, 20), (2024, 0, 40), (1999, 1, 7))]
    pd.DataFrame(rows, columns=["rok_hlaseni", "rok_incidence", "kvartal_incidence", "okres_kod", "kraj_kod",
                                "region", "CZ", "vek_nazev", "vek_kod", "pripady"]).to_csv(workspace / "tbc.csv", index=False)
    g.tbc_registry()

    origin = {d["label"]: d["data"] for d in _read(workspace, "tbc_origin")["datasets"]}
    assert origin["Narození v zahraničí"] == [0, 0, 40]
    assert _read(workspace, "tbc_origin")["labels"] == ["2022", "2023", "2024"]      # 1999 se nepočítá
    tbc_map = _read(workspace, "tbc_map")
    assert tbc_map["years_averaged"] == 3 and tbc_map["regions"]["CZ010"] == 4.0     # (30+30+60)/3 na 1 mil.

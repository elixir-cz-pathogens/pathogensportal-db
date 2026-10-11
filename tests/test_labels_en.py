"""Anglické popisky grafů (labels_en.py, curated/labels_en.yaml)."""

from pathlib import Path

import pandas as pd
import pytest

import generate_json
import labels_en

DATA = Path(__file__).resolve().parent.parent / "data"


def test_fixed_labels_translate():
    assert labels_en.translate("Případy") == "Cases"
    assert labels_en.translate("Plané neštovice [varicella]") == "Varicella [chickenpox]"
    assert labels_en.translate("Kraj Vysočina") == "Vysočina"
    assert labels_en.translate("Praha + Stř. Čechy") == "Prague + Central Bohemia"


@pytest.mark.parametrize("cs, en", [
    ("KT 40/25", "Wk 40/25"),
    ("0 let", "Under 1 year"),
    ("1–4 roky", "1–4 years"),
    ("75+ let", "75+ years"),
    ("18 let a méně", "18 years and under"),
    ("65 let a více", "65 years and over"),
    ("Věková skupina (2021–2025)", "Age group (2021–2025)"),
    ("DRK 2026", "DRC 2026"),
])
def test_patterns(cs, en):
    assert labels_en.translate(cs) == en


@pytest.mark.parametrize("text", ["2024-01-01", "2021/22", "0–9", "80+", "RSV", "SARS-CoV-2"])
def test_no_translation_needed(text):
    assert labels_en.translate(text) == text


def test_unknown_czech_label_is_reported():
    assert labels_en.translate("Neznámý popisek") is None
    obj, missing = labels_en.localize({"labels": ["2024"], "datasets": [{"label": "Neznámý popisek"}]})
    assert missing == ["Neznámý popisek"]
    assert "label_en" not in obj["datasets"][0]


def test_localize_adds_en_fields_and_keeps_czech():
    obj, missing = labels_en.localize({
        "labels": ["0 let", "1–4 roky"],
        "x_title": "Rok hlášení",
        "unit": "na 100 000 obyvatel",
        "datasets": [{"label": "Syfilis"}, {"label": "RSV"}],
    })
    assert missing == []
    assert obj["labels"] == ["0 let", "1–4 roky"]
    assert obj["labels_en"] == ["Under 1 year", "1–4 years"]
    assert obj["x_title_en"] == "Year of notification"
    assert obj["unit_en"] == "per 100,000 population"
    assert obj["datasets"][0]["label_en"] == "Syphilis"
    assert "label_en" not in obj["datasets"][1]       # beze změny → bez pole


def test_localize_without_change_adds_nothing():
    obj, _ = labels_en.localize({"labels": ["2024-01-01"], "datasets": [{"label": "RSV"}]})
    assert "labels_en" not in obj


def test_map_labels_by_region_code():
    obj, missing = labels_en.localize({"labels": dict(generate_json.NUTS3_NAMES)})
    assert missing == []
    assert obj["labels_en"]["CZ010"] == "Prague"
    assert obj["labels_en"]["CZ080"] == "Moravia-Silesia"


def test_signal_annotated_by_codes():
    rec = labels_en.annotate_signal({"diagnoza": "B15", "diagnoza_nazev": "Akutní hepatitida A",
                                     "kraj_kod": "CZ064", "kraj_nazev": "Jihomoravský kraj"})
    assert rec["diagnoza_nazev_en"] == "Acute hepatitis A"
    assert rec["kraj_nazev_en"] == "South Moravian Region"
    rec = labels_en.annotate_signal({"diagnoza": "B15", "kraj_kod": "CZ"})
    assert rec["kraj_nazev_en"] == "Czechia"


def test_every_region_in_code_has_english_name():
    names = list(generate_json.NUTS3_NAMES.values()) + list(generate_json.REGION_SHORT) \
        + list(generate_json.REGION_SHORT.values())
    assert [n for n in names if labels_en.translate(n) is None] == []


def test_every_fixed_label_in_code_has_english_name():
    fixed = [label for _, label in generate_json.VAX_COLS] + list(generate_json.STI_SERIES.values()) \
        + generate_json.STI_AGE_ORDER + generate_json.TBC_AGE_ORDER \
        + [d for g in generate_json.DISEASE_GROUPS.values() for d in g["diseases"]]
    assert [x for x in fixed if labels_en.translate(x) is None] == []


@pytest.mark.parametrize("path, code, name", [
    ("isin/isin_infekcni_nemoci.csv", "diagnoza", "diagnoza_nazev"),
    ("uzis/uzis_pohlavni_nemoci.csv", "diagnoza_kod", "diagnoza_nazev"),
])
def test_every_diagnosis_in_data_has_english_name(path, code, name):
    """Nová diagnóza v datech ÚZIS musí dostat anglický název v labels_en.yaml."""
    f = DATA / path
    if not f.exists():
        pytest.skip(f"{path} není stažený")
    df = pd.read_csv(f, usecols=[code, name]).drop_duplicates()
    missing = sorted(c for c in df[code].unique() if labels_en.diagnosis(c) is None)
    assert missing == [], f"doplň do curated/labels_en.yaml: {missing}"
    # český název se v grafech hledá podle textu — musí sedět s daty
    known = labels_en._load()["diagnoses"]
    renamed = sorted(f"{c}: {n!r}" for c, n in zip(df[code], df[name]) if known[c]["cs"] != n)
    assert renamed == [], f"ÚZIS přejmenoval diagnózu, oprav `cs` v labels_en.yaml: {renamed}"


def test_every_region_in_isin_has_english_name():
    f = DATA / "isin" / "isin_infekcni_nemoci.csv"
    if not f.exists():
        pytest.skip("ISIN není stažený")
    df = pd.read_csv(f, usecols=["kraj_kod", "kraj_nazev"]).drop_duplicates()
    df = df[df.kraj_kod != "CZ999"]          # „neuvedeno“ se v grafech neukazuje
    assert [k for k in df.kraj_kod if labels_en.region(k) is None] == []
    assert [n for n in df.kraj_nazev if labels_en.translate(n) is None] == []

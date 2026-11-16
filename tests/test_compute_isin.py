"""Přehledové bloky infekčních nemocí (compute_isin.py) — týdenní blok a roční přehled.

Hlídá hlavně jedno pravidlo: od července 2025 vzniká část záznamů z hlášení EWS,
takže počet diagnózy, u které EWS tvoří víc než desetinu, se NESMÍ srovnat
s předchozím rokem — místo srovnání musí být upozornění. Zbytek hlídá, že čísla
v textu jsou ta z dat a že se soubor nepřepisuje, když se data nezměnila.
"""
import json
import re

import pandas as pd
import pytest

import compute_isin as ci

# Slova, která by z počtu udělala tvrzení o vývoji nemocnosti. U diagnózy s EWS
# nesmí zaznít nikde kromě věty, která říká, že srovnávat NELZE.
COMPARISON = re.compile(r"\bvíc\b|\bméně\b|vzrost|kles|nárůst|pokles|\bmore\b|\bfewer\b|\brise\b|\bfell\b",
                        re.IGNORECASE)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for name, rel in (("WEEKLY_FILE", "isin_tydenni.csv"), ("WEEKLY_STATE", "isin_tydenni_stav.json"),
                      ("ISIN_FILE", "isin.csv"), ("POP_AGE_FILE", "population_age.csv")):
        monkeypatch.setattr(ci, name, tmp_path / rel)
    monkeypatch.setattr(ci, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    # 100 obyvatel v každém roce věku 0–100, v obou letech
    pd.DataFrame([{"rok": y, "vek": a, "pocet": 100} for y in (2024, 2025) for a in range(101)]
                 ).to_csv(tmp_path / "population_age.csv", index=False)
    return tmp_path


def _weekly(ws, series):
    """`series` = {kód: {rok: [(pocet, ews), …]}}; věk: polovina do „0 let“, zbytek do „75 a více let“."""
    rows = []
    for code, years in series.items():
        for year, weeks in years.items():
            for w, (n, ews) in enumerate(weeks, 1):
                young = n // 2
                for vek, count, e in (("0 let", young, 0), ("75 a více let", n - young, ews), ("Celkem", n, ews)):
                    rows.append({"diagnoza": code, "kraj_kod": "CZ", "rok": year, "tyden": w,
                                 "vek": vek, "pocet": count, "pocet_ews": e})
                rows.append({"diagnoza": code, "kraj_kod": "CZ010", "rok": year, "tyden": w,
                             "vek": "Celkem", "pocet": 1, "pocet_ews": 0})        # kraje se do bloku nepočítají
    pd.DataFrame(rows).to_csv(ws / "isin_tydenni.csv", index=False)
    (ws / "isin_tydenni_stav.json").write_text(json.dumps({
        "slozka": "2026-03-09", "stav_k": "2026-03-08T05:34", "fetched_at": "2026-03-09T06:00:00Z",
        "diagnozy": {code: {"cs": code.upper(), "en": code} for code in series}}), encoding="utf-8")


def test_diagnoza_s_ews_se_nesrovnava_s_lonskem(ws):
    _weekly(ws, {"B02": {2025: [(10, 0)] * 52, 2026: [(40, 30)] * 10},      # 75 % z EWS
                 "B15": {2025: [(20, 0)] * 52, 2026: [(30, 1)] * 10}})      # 3,3 % z EWS
    out = ci.build_weekly()
    by = {d["code"]: d for d in out["diseases"]}

    zoster = by["B02"]
    assert zoster["comparable"] is False and zoster["ytd"]["ews_share"] == 75.0
    ids = {f["id"] for f in zoster["facts"]["facts"]}
    assert "ews_break" in ids and "prev" not in ids
    assert "ews_break" in zoster["facts"]["required"]
    for lang in ("cs", "en"):
        for s in zoster["comment"]["sentences"]:
            if "ews_break" in s["facts"]:
                assert re.search(r"srovnávat nelze|cannot be compared", s[lang])
            else:
                assert not COMPARISON.search(s[lang]), s[lang]

    hepa = by["B15"]
    assert hepa["comparable"] is True
    assert hepa["facts"]["values"]["n_prev"]["raw"] == 200          # stejných 10 týdnů roku 2025
    assert hepa["facts"]["values"]["direction"]["cs"] == "víc"      # 300 proti 200
    assert "ews_break" not in {f["id"] for f in hepa["facts"]["facts"]}


def test_tydenni_blok_cisla_a_datum(ws):
    _weekly(ws, {"B02": {2025: [(10, 0)] * 52, 2026: [(40, 30)] * 9 + [(1, 0)]},
                 "B15": {2025: [(20, 0)] * 52, 2026: [(30, 1)] * 10}})
    out = ci.build_weekly()
    assert [d["code"] for d in out["diseases"]] == ["B02", "B15"]      # podle počtu od začátku roku
    assert out["week"] == "2026-W10" and out["year"] == 2026 and out["years"] == [2025, 2026]
    # Stránka má ukazovat den, ke kterému zdroj data uzavřel, ne den běhu pipeline.
    assert out["posledni_datum"] == "2026-03-08" and "generated_at" not in out
    zoster = out["diseases"][0]
    assert zoster["ytd"]["n"] == 361 and zoster["last_week"] == {"n": 1, "ews": 0}
    assert zoster["last4"] == {"n": 121, "previous": 160}
    assert zoster["weeks"]["2026"]["ews"][:2] == [30, 30] and len(zoster["weeks"]["2025"]["n"]) == 52
    assert "1\u00a0hlášený případ." in zoster["comment"]["cs"] and "1 reported case " in zoster["comment"]["en"]
    assert zoster["name"]["cs"] == "Pásový opar" and zoster["official"]["cs"] == "B02"
    # věk: posledních 52 týdnů = 42 týdnů roku 2025 + 10 týdnů roku 2026, na 100 tis. obyvatel skupiny
    young = zoster["age"][0]
    assert young["cs"] == "0 let" and young["n"] == 42 * 5 + 9 * 20 + 0
    assert young["rate"] == round(young["n"] / 100 * 100_000, 1)
    assert zoster["age"][1]["cs"] == "75+ let" and zoster["age"][1]["en"] == "75+ years"
    assert out["age_window"]["from"] == "2025-W11" and out["age_window"]["population_year"] == 2025


def test_bez_stazeneho_souhrnu_blok_nevznikne(ws):
    assert ci.build_weekly() is None


def _isin(ws, rows):
    cols = ["rok", "mesic", "kraj_kod", "diagnoza", "diagnoza_nazev", "vek_kod", "vek_nazev", "EWS", "pocet_pripadu"]
    pd.DataFrame(rows, columns=cols).to_csv(ws / "isin.csv", index=False, encoding="utf-8-sig")


def test_rocni_prehled(ws):
    _isin(ws, [
        (2024, 5, "CZ010", "B02", "Pásový opar [herpes zoster]", 66075999, "75+ let", 0, 100),
        (2025, 5, "CZ010", "B02", "Pásový opar [herpes zoster]", 66075999, "75+ let", 60, 300),
        (2025, 12, "CZ010", "A02", "Jiné infekce způsobené salmonelami", 66001004, "1–4 roky", 2, 400),
        (2025, 6, "CZ010", "J10", "Chřipka způsobená identifikovaným sezónním chřipkovým virem", 66001004, "1–4 roky", 0, 9000),
        (2025, 6, "CZ010", "Z99", "Zcela neznámá diagnóza [test]", 66040044, "40–44 let", 0, 5),
    ])
    (ws / "out" / "isin_regional_incidence.json").write_text(json.dumps(
        {"year": 2025, "regions": {"CZ010": 720.6, "CZ063": 1639.9, "CZ080": 1511.6}}), encoding="utf-8")
    out = ci.build_overview()
    assert out["posledni_obdobi"] == "2025-12" and out["excluded"] == ["J10"]

    top = out["blocks"]["top"]
    assert [e["code"] for e in top["by_year"]["2025"]] == ["A02", "B02", "Z99"]     # chřipka se vynechává
    zoster = top["by_year"]["2025"][1]
    assert zoster == {"code": "B02", "name": {"cs": "Pásový opar", "en": "Shingles"},
                      "official": zoster["official"], "n": 300, "ews": 60, "ews_share": 20.0, "previous": 100}
    assert zoster["official"]["cs"] == "Pásový opar [herpes zoster]"
    assert top["by_year"]["2025"][2]["name"]["cs"] == "Zcela neznámá diagnóza"     # bez záznamu = úřední název
    assert top["years"] == ["2025", "2024"]
    assert "salmonelóza (400\u00a0hlášených případů)" in top["comment"]["cs"]
    # upozornění na EWS jmenuje jen diagnózy nad mezí
    assert "pásový opar" in top["comment"]["cs"] and "ews_top" in top["facts"]["required"]
    assert top["facts"]["values"]["ews_names"]["en"] == "shingles"

    age = out["blocks"]["age"]
    assert [g["cs"] for g in age["groups"]] == ["1–4 roky", "40–44 let", "75+ let"]
    assert age["groups"][0]["rate"] == round(400 / 400 * 100_000, 1)               # 4 roky věku × 100 lidí
    assert age["groups"][2]["rate"] == round(300 / 2600 * 100_000, 1)              # věk 75–100
    assert age["population_year"] == 2025
    assert "1–4 roky" in age["comment"]["cs"] and "salmonelóza" in age["comment"]["cs"]

    regions = out["blocks"]["regions"]
    assert "Kraj Vysočina (1\u00a0639,9)" in regions["comment"]["cs"]
    assert "Praha (720,6)" in regions["comment"]["cs"] and "Prague (720.6)" in regions["comment"]["en"]


def test_bez_mapy_a_bez_obyvatel_se_vynechaji_jen_ty_bloky(ws):
    _isin(ws, [(2025, 5, "CZ010", "A02", "Jiné infekce způsobené salmonelami", 66001004, "1–4 roky", 0, 4)])
    (ws / "population_age.csv").unlink()
    assert set(ci.build_overview()["blocks"]) == {"top"}


def test_bez_zmeny_dat_se_soubor_neprepisuje(ws):
    _weekly(ws, {"B15": {2025: [(20, 0)] * 52, 2026: [(30, 1)] * 10}})
    ci.save("isin_weekly", ci.build_weekly())
    path = ws / "out" / "isin_weekly.json"
    first = path.read_text(encoding="utf-8")
    assert json.loads(first)["meta"]["generated_at"]
    path.write_text(first.replace(json.loads(first)["meta"]["generated_at"], "2000-01-01T00:00:00Z"), encoding="utf-8")
    ci.save("isin_weekly", ci.build_weekly())
    assert "2000-01-01T00:00:00Z" in path.read_text(encoding="utf-8")              # nepřepsáno
    _weekly(ws, {"B15": {2025: [(20, 0)] * 52, 2026: [(30, 1)] * 11}})
    ci.save("isin_weekly", ci.build_weekly())
    assert "2000-01-01T00:00:00Z" not in path.read_text(encoding="utf-8")          # nový týden → nový soubor


def test_skupiny_rocni_rady_a_pravidlo_pro_zmenu(ws):
    rows = []
    for year, zoster, pertussis, rash, tul, sepsis in ((2024, 300, 900, 500, 60, 40), (2025, 900, 100, 50, 70, 45)):
        rows += [
            (year, 5, "CZ010", "B02", "Pásový opar [herpes zoster]", 66075999, "75+ let", 600 if year == 2025 else 0, zoster),
            (year, 5, "CZ010", "A37", "Dávivý kašel [pertussis]", 66001004, "1–4 roky", 0, pertussis),
            (year, 5, "CZ010", "B08", "Jiné virové infekce charakterizované postižením kůže a sliznic NJ", 66001004, "1–4 roky", 0, rash),
            (year, 5, "CZ010", "A21", "Tularemie", 66040044, "40–44 let", 0, tul),
            (year, 5, "CZ010", "A41", "Jiná sepse", 66075999, "75+ let", 0, sepsis),
            (year, 5, "CZ010", "J10", "Chřipka způsobená identifikovaným sezónním chřipkovým virem", 66001004, "1–4 roky", 0, 9000),
        ]
    _isin(ws, rows)
    out = ci.build_groups()
    assert out["years"] == [2024, 2025] and out["year"] == 2025

    child = out["groups"]["childhood_airborne"]
    # černý kašel a virové vyrážkové infekce patří k dětským, ne do „Ostatních“
    assert [d["code"] for d in child["diseases"]] == ["B02", "A37", "B08"]
    zoster = child["diseases"][0]
    assert zoster["n"] == [300, 900] and zoster["ews"] == [0, 600] and zoster["ews_share"] == 66.7
    assert zoster["name"]["cs"] == "Pásový opar" and zoster["official"]["cs"].startswith("Pásový opar [")
    # Největší skok má pásový opar, ale ze dvou třetin je to EWS — do věty o změně nesmí.
    # Z poctivě srovnatelných vyhrává větší rozdíl v počtu (900 → 100), ne větší poměr (500 → 50).
    assert child["facts"]["values"]["chg_name"]["cs"] == "Černý kašel"
    assert child["facts"]["values"]["n_chg_prev"]["raw"] == 900
    assert "pásový opar" in child["facts"]["values"]["ews_names"]["cs"]
    assert "ews_top" in child["facts"]["required"]
    assert child["title"] == {"cs": "Časté dětské/vzdušné", "en": "Childhood/airborne"}
    # věková skupina, ve které skupina nemocí žádný případ nemá, zůstane v grafu s nulou
    assert [(g["cs"], g["n"]) for g in child["age"]["groups"]] == [("1–4 roky", 150), ("40–44 let", 0), ("75+ let", 900)]
    assert child["age"]["groups"][1]["rate"] == 0.0 and child["age"]["groups"][1]["top"] is None

    # tularemie je u nákaz od zvířat
    assert [d["code"] for d in out["groups"]["vector_animal"]["diseases"]] == ["A21"]
    assert "rare_severe" not in out["groups"]                      # skupina bez dat se nevypíše

    other = out["groups"]["other"]
    assert [e["code"] for e in other["by_year"]["2025"]] == ["A41"]       # jen nezařazené, bez chřipky
    assert other["total"] == [40, 45] and other["diagnoses"] == 1


def test_tydenni_diagnoza_zna_svou_skupinu():
    groups = ci.group_of_code()
    assert groups["B01"] == "childhood_airborne" and groups["A37"] == "childhood_airborne"
    assert groups["A04"] == "gastrointestinal" and groups["A69"] == "vector_animal"
    assert groups["A21"] == "vector_animal" and groups["B15"] == "hepatitis"
    # každá diagnóza skupin musí mít kód, jinak by týdenní blok skupinu nenašel
    names = {name for spec in ci.disease_groups().values() for name in spec["diseases"]}
    known = {d["cs"] for d in ci.labels_en._load()["diagnoses"].values()}
    assert not names - known, names - known


@pytest.mark.parametrize("n, cs, en", [
    (0, "žádný hlášený případ", "no reported cases"),
    (1, "1 hlášený případ", "1 reported case"),
    (3, "3 hlášené případy", "3 reported cases"),
    (1234, "1 234 hlášených případů", "1,234 reported cases"),
])
def test_tvar_poctu_pripadu(n, cs, en):
    assert ci.cases(n) == (cs, en)


def test_tyden_se_pise_datem():
    assert ci.week_range(2026, 46, "cs") == "9.–15. listopadu"
    assert ci.week_range(2026, 46, "en", with_year=True) == "9–15 November 2026"
    assert ci.week_range(2026, 1, "cs", with_year=True) == "29. prosince – 4. ledna 2026"


def test_vekove_skupiny_obou_zdroju():
    assert ci.isin_age_bounds(66000000) == (0, 0) and ci.isin_age_bounds(66001004) == (1, 4)
    assert ci.isin_age_bounds(66075999) == (75, None)
    assert ci.weekly_age_bounds("0 let") == (0, 0) and ci.weekly_age_bounds("25 až 34 let") == (25, 34)
    assert ci.weekly_age_bounds("75 a více let") == (75, None)
    assert ci.age_label(0, 0) == {"cs": "0 let", "en": "Under 1 year"}
    assert ci.age_label(1, 4)["cs"] == "1–4 roky" and ci.age_label(25, 34)["en"] == "25–34 years"
    with pytest.raises(ValueError):
        ci.weekly_age_bounds("dospělí")


def test_kratka_jmena_nezuzuji_kod():
    names = ci._names()
    # A04 jsou všechny „jiné bakteriální střevní infekce“, kampylobakter je jen jejich část.
    assert "další" in names["A04"]["cs"] and "others" in names["A04"]["en"]
    assert names["A04.5"]["cs"] == "Kampylobakterióza"
    assert "jiné" in names["A69"]["cs"] and names["A69.2"]["cs"] == "Lymeská borelióza"
    for code, name in names.items():
        assert name.get("cs") and name.get("en"), code

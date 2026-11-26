"""Respirační bloky (compute_resp.py) — přehled týdne, věk, typy chřipky a jejich komentáře.

Hlídá pravidla, která se v textu rozbijí potichu: sváteční týden bez upozornění,
„převažuje“ z hrstky vzorků, srovnání se dvěma sezónami, podíl z pár vzorků.
"""
import json
import re

import pandas as pd
import pytest

import compute_resp as cr


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(cr, "RATES_FILE", tmp_path / "rates.csv")
    monkeypatch.setattr(cr, "LAB_FILE", tmp_path / "lab.csv")
    monkeypatch.setattr(cr, "HOLIDAYS_FILE", tmp_path / "holidays.csv")
    monkeypatch.setattr(cr, "FLU_MEM_FILE", tmp_path / "out" / "flu_mem.json")
    monkeypatch.setattr(cr, "COVID_WEEKLY_FILE", tmp_path / "out" / "covid_cases_weekly.json")
    monkeypatch.setattr(cr, "COVID_SUMMARY_FILE", tmp_path / "out" / "covid_summary.json")
    monkeypatch.setattr(cr, "ESURV_DAILY_FILE", tmp_path / "esurv_denni.csv")
    monkeypatch.setattr(cr, "ESURV_STATE_FILE", tmp_path / "esurv_stav.json")
    return tmp_path


def _rates(ws, rows):
    """rows: (tyden_iso, ukazatel, vek, mira)"""
    pd.DataFrame(rows, columns=["tyden_iso", "ukazatel", "vek", "mira_na_100k"]).to_csv(ws / "rates.csv", index=False)
    return cr.load_rates()


def _lab(ws, rows):
    """rows: (tyden_iso, typ, subtyp, ukazatel, hodnota) — věk total"""
    full = [(w, typ.split()[0], typ, sub, ind, "total", v) for w, typ, sub, ind, v in rows]
    pd.DataFrame(full, columns=["tyden_iso", "patogen", "typ", "subtyp", "ukazatel", "vek", "hodnota"]
                 ).to_csv(ws / "lab.csv", index=False)
    return cr.load_lab()


def _holidays(ws, rows):
    """rows: (iso_year, iso_week, public_holidays, school_days_off_excl_public)"""
    pd.DataFrame([(y, w, "x", p + s, p, s) for y, w, p, s in rows],
                 columns=["iso_year", "iso_week", "week_start", "school_days_off", "public_holidays",
                          "school_days_off_excl_public"]).to_csv(ws / "holidays.csv", index=False)
    return cr.load_holidays()


def _flu_week(week, det, tests, rsv=0):
    return [(week, "Influenza", "total", "detections", det), (week, "Influenza", "total", "tests", tests),
            (week, "RSV", "RSV", "detections", rsv), (week, "RSV", "RSV", "tests", tests)]


MEM = {"week": "2026-W45", "level": "baseline", "threshold": 49.42,
       "trend": {"category": "stable", "p_growth": 0.68}}


def _overview_inputs(ws, last_ili=23.3, lab_last=(10, 859)):
    rates = _rates(ws, [(w, ind, "total", v) for w, ili, ari in
                        [("2026-W43", 18.7, 837.9), ("2026-W44", 14.4, 646.5), ("2026-W45", last_ili, 854.8)]
                        for ind, v in (("ILI", ili), ("ARI", ari))])
    lab = _lab(ws, _flu_week("2026-W43", 4, 798) + _flu_week("2026-W44", 13, 741) + _flu_week("2026-W45", *lab_last))
    return rates, lab


# ── Fakta a komentář obecně ──────────────────────────────────────────────────

def test_komentar_nesmi_vynechat_povinny_fakt():
    f = cr.Facts()
    f.fact("a", "measurement", "x", "x", required=True)
    with pytest.raises(ValueError, match="povinná"):
        f.block()


def test_komentar_nesmi_odkazovat_na_neexistujici_fakt():
    f = cr.Facts()
    f.say("x", "x", ["neni"])
    with pytest.raises(ValueError, match="neexistující"):
        f.block()


def test_cisla_se_formatuji_cesky_i_anglicky():
    assert cr.fmt_num(3139.6, "cs") == "3 139,6" and cr.fmt_num(3139.6, "en") == "3,139.6"
    assert cr.fmt_pct(2.2, "cs") == "2,2 %" and cr.fmt_pct(2.2, "en") == "2.2%"


def test_tyden_se_pise_datem_ne_cislem():
    """Čtenář v kalendářních týdnech nepřemýšlí: „2.–8. listopadu“, ne „KT 45/26“."""
    assert cr.week_label("2026-W45", "cs") == "2.–8.\u00a0listopadu"
    assert cr.week_label("2026-W45", "en") == "2–8\u00a0November"
    # týden přes hranici měsíce i roku
    assert cr.week_label("2026-W44", "cs") == "26.\u00a0října – 1.\u00a0listopadu"
    assert cr.week_label("2026-W53", "en") == "28\u00a0December – 3\u00a0January"


def test_osa_sezony_ma_tyden_53_jen_kdyz_ho_rok_ma():
    assert "2026-W53" in cr.season_axis(2026) and len(cr.season_axis(2026)) == 34
    assert "2025-W53" not in cr.season_axis(2025) and len(cr.season_axis(2025)) == 33
    assert cr.season_of("2026-W39") == 2025 and cr.season_of("2026-W40") == 2026


# ── Přehled týdne ────────────────────────────────────────────────────────────

def test_prehled_bere_prah_a_trend_z_flu_mem(ws):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM)
    ili = out["tiles"][0]
    assert (ili["value"], ili["level"], ili["threshold"], ili["trend"]["category"]) == (23.3, "baseline", 49.4, "stable")
    assert {"ili_now", "level_now", "trend", "flu_pos", "caveat_provisional"} <= set(out["facts"]["required"])
    assert "pod epidemickým prahem" in out["comment"]["cs"] and "pravděpodobně stabilní" in out["comment"]["cs"]
    assert "likely stable" in out["comment"]["en"]


def test_prehled_bez_flu_mem_nema_pasmo_ani_trend(ws):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, {})
    assert "level" not in out["tiles"][0] and "trend" not in out["tiles"][0]
    assert "prahem" not in out["comment"]["cs"] and "Trend" not in out["comment"]["cs"]


def test_prah_z_jineho_tydne_se_nepouzije(ws):
    """flu_mem.json o týden starší než data: pásmo by patřilo jinému číslu."""
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, {**MEM, "week": "2026-W44"})
    assert "level" not in out["tiles"][0]


def test_smer_pozitivity_az_od_pul_procentniho_bodu(ws):
    rates, lab = _overview_inputs(ws)                    # 13/741 = 1,8 % → 10/859 = 1,2 %
    assert cr.build_overview(rates, lab, {}, MEM)["tiles"][2]["direction"] == "lower"
    rates, lab = _overview_inputs(ws, lab_last=(13, 859))  # 1,5 % — rozdíl 0,3 bodu
    out = cr.build_overview(rates, lab, {}, MEM)
    assert out["tiles"][2]["direction"] == "same" and "beze změny" in out["comment"]["cs"]


def test_malo_vzorku_podil_neuvadi(ws):
    rates, lab = _overview_inputs(ws, lab_last=(3, 12))
    out = cr.build_overview(rates, lab, {}, MEM)
    assert out["tiles"][2]["value"] is None
    assert "caveat_low_n" in out["facts"]["required"] and "flu_pos" not in out["facts"]["required"]
    assert "podíl pozitivních na chřipku se proto neuvádí" in out["comment"]["cs"]


def test_svatecni_tyden_nese_upozorneni(ws):
    rates, lab = _overview_inputs(ws)
    hol = _holidays(ws, [(2026, 44, 0, 0), (2026, 45, 1, 2.0)])
    out = cr.build_overview(rates, lab, hol, MEM)
    assert out["tiles"][0]["holiday"] == "both" and "caveat_holiday" in out["facts"]["required"]
    assert "sváteční týden (státní svátek a školní prázdniny)" in out["comment"]["cs"]


def test_tyden_po_svatecnim_nese_upozorneni_na_rozdil(ws):
    """14,4 → 23,3 po podzimních prázdninách je návrat hlášení, ne nárůst nemocnosti."""
    rates, lab = _overview_inputs(ws)
    hol = _holidays(ws, [(2026, 44, 1, 2.0), (2026, 45, 0, 0)])
    out = cr.build_overview(rates, lab, hol, MEM)
    assert out["tiles"][0]["holiday"] is None and out["tiles"][0]["prev_holiday"] == "both"
    assert "caveat_holiday_prev" in out["facts"]["required"]
    assert "není změna nemocnosti" in out["comment"]["cs"]


def test_tyden_mimo_tabulku_svatku_neni_oznacen_jako_bezny(ws):
    assert cr.holiday_kind("2030-W10", {"2026-W44": {"public": 1, "school": 2.0}}) is None
    assert cr.holiday_kind("2026-W44", {"2026-W44": {"public": 0, "school": 0.8}}) == ""


def test_posledni_laboratorni_tydny_jsou_predbezne(ws):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM)
    assert out["provisional_weeks"] == ["2026-W44", "2026-W45"]
    flags = {p["week"]: p.get("provisional", False) for p in out["tiles"][2]["series"]}
    assert flags["2026-W45"] and flags["2026-W44"] and not flags["2026-W43"]


def _covid(ws, last_day, rows=(("2026-10-26", 346), ("2026-11-02", 560), ("2026-11-09", 708))):
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "covid_cases_weekly.json").write_text(json.dumps(
        {"labels": [r[0] for r in rows], "datasets": [{"label": "Nové případy (týden)", "data": [r[1] for r in rows]}]}),
        encoding="utf-8")
    if last_day:
        (ws / "out" / "covid_summary.json").write_text(json.dumps({"posledni_datum": last_day}), encoding="utf-8")
    return cr.covid_weekly()


def test_covid_bere_jen_uplne_tydny(ws):
    """Běžící týden (data do čtvrtka) by vedle úplného vypadal jako skok nebo propad."""
    assert list(_covid(ws, "2026-11-12").index) == ["2026-W44", "2026-W45"]
    assert list(_covid(ws, "2026-11-15").index) == ["2026-W44", "2026-W45", "2026-W46"]


def test_covid_bez_data_posledniho_dne_vynecha_posledni_tyden(ws):
    assert list(_covid(ws, None).index) == ["2026-W44", "2026-W45"]


def test_prehled_ma_dlazdici_covidu_a_veta_se_vejde_jen_nekdy(ws):
    rates, lab = _overview_inputs(ws)
    covid = _covid(ws, "2026-11-12")
    out = cr.build_overview(rates, lab, {}, MEM, covid)
    tile = out["tiles"][-1]
    assert (tile["id"], tile["value"], tile["prev"], tile["change_pct"], tile["week"]) == ("covid", 560, 346, 61.8, "2026-W45")
    assert "Nově potvrzených případů covidu-19 bylo ve stejném týdnu 560." in out["comment"]["cs"]
    # S upozorněním na týden po svátečním by komentář přetekl — nepovinná věta o covidu vypadne.
    hol = _holidays(ws, [(2026, 44, 1, 2.0), (2026, 45, 0, 0)])
    out = cr.build_overview(rates, lab, hol, MEM, covid)
    assert "covid" not in out["comment"]["cs"] and len(out["comment"]["cs"].split()) <= cr.MAX_COMMENT_WORDS
    assert out["tiles"][-1]["id"] == "covid"            # dlaždice zůstává


def test_prehled_bez_grafu_covidu_dobehne(ws):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM, cr.covid_weekly())
    assert [t["id"] for t in out["tiles"]] == ["ili", "ari", "flu_pos", "rsv_pos"]


# ── Nemocnice (denní e-surveillance ÚZIS) ────────────────────────────────────

def _hospital(ws, last_day, per_day=100):
    """Denní řada od pondělí 26. 10. 2026 do `last_day`; týden 45 má o polovinu víc příjmů."""
    from datetime import date, timedelta
    days, d = [], date(2026, 10, 26)
    while d <= date.fromisoformat(last_day):
        days.append(d)
        d += timedelta(days=1)
    rows = [(x.isoformat(), "ari_ili", "CZ", "celkem", per_day * (1.5 if x.isocalendar()[1] == 45 else 1))
            for x in days]
    rows += [(x.isoformat(), "chripka", "CZ", "celkem", 1) for x in days]             # jiná nemoc se nesčítá
    rows += [(x.isoformat(), "ari_ili", "CZ010", "celkem", 9) for x in days]          # kraj se nesčítá
    rows += [(x.isoformat(), "ari_ili", "CZ", "75+", 7) for x in days]                # věk se nesčítá
    pd.DataFrame(rows, columns=["datum", "nemoc", "kraj_kod", "vek", "hosp_nove"]).to_csv(ws / "esurv_denni.csv",
                                                                                          index=False)
    (ws / "esurv_stav.json").write_text(json.dumps({"stav_k": last_day}), encoding="utf-8")
    return cr.hospital_weekly()


def test_nemocnice_berou_jen_uplne_tydny_cele_republiky(ws):
    """Běžící týden (data do čtvrtka) by vedle úplného vypadal jako propad."""
    assert _hospital(ws, "2026-11-12").to_dict() == {"2026-W44": 700.0, "2026-W45": 1050.0}
    assert list(_hospital(ws, "2026-11-15").index) == ["2026-W44", "2026-W45", "2026-W46"]


def test_prehled_ma_dlazdici_nemocnic_s_vlastni_jednotkou(ws):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM, None, _hospital(ws, "2026-11-12"))
    tile = out["tiles"][-1]
    assert (tile["id"], tile["unit"], tile["value"], tile["prev"], tile["change_pct"], tile["week"]) == \
        ("hosp", "admissions", 1050, 700, 50.0, "2026-W45")
    assert [p["value"] for p in tile["series"] if p["week"] in ("2026-W44", "2026-W45")] == [700.0, 1050.0]
    assert "Nově přijatí do nemocnice s akutní respirační infekcí ve stejném týdnu: 1\u00a0050." in out["comment"]["cs"]
    source = next(f for f in out["facts"]["facts"] if f["id"] == "source")
    assert "hospitalizace ÚZIS" in source["cs"] and "hospitalisations from ÚZIS" in source["en"]


def test_nemocnice_s_novejsim_tydnem_nez_ili_ho_jmenuji(ws):
    """E-surveillance bývá o týden napřed před ERVISS — věta musí říct, o kterém týdnu mluví."""
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM, None, _hospital(ws, "2026-11-15"))
    assert out["tiles"][-1]["week"] == "2026-W46" and out["week"] == "2026-W45"
    # Věta je nepovinná (limit 80 slov), ale fakt i jeho týden v balíčku jsou vždy.
    values = out["facts"]["values"]
    assert values["hosp_when"]["cs"] == "v týdnu 9.–15.\u00a0listopadu" and values["hosp_new"]["raw"] == 700
    assert len(out["comment"]["cs"].split()) <= cr.MAX_COMMENT_WORDS


def test_prehled_bez_e_surveillance_dobehne_a_zdroj_ji_neuvadi(ws, capsys):
    rates, lab = _overview_inputs(ws)
    out = cr.build_overview(rates, lab, {}, MEM, None, cr.hospital_weekly())
    assert "hosp" not in [t["id"] for t in out["tiles"]]
    assert "ÚZIS" not in next(f for f in out["facts"]["facts"] if f["id"] == "source")["cs"]
    assert "bez nemocnic" in capsys.readouterr().out


# ── Věk ──────────────────────────────────────────────────────────────────────

def _age_rates(ws, seasons, last=3000.0):
    rows = []
    for s in seasons:
        for w in cr.season_axis(s):
            for age, base in (("0-4", 2000.0 + 100 * (s - 2022)), ("5-14", 900.0), ("15-64", 500.0), ("65+", 300.0),
                              ("total", 600.0)):
                rows.append((w, "ARI", age, base))
    for w in ("2026-W40", "2026-W41"):
        for age, v in (("0-4", last), ("5-14", 950.0), ("15-64", 520.0), ("65+", 310.0), ("total", 640.0)):
            rows.append((w, "ARI", age, v))
    return _rates(ws, rows)


def test_vek_rozpeti_az_od_tri_sezon(ws):
    out = cr.build_age(_age_rates(ws, [2024, 2025]), {})
    g = out["indicators"]["ari"]["groups"][0]
    assert g["min"][0] is None and "age_hist" not in [f["id"] for f in out["indicators"]["ari"]["facts"]["facts"]]
    assert "předchozích sezón" not in out["indicators"]["ari"]["comment"]["cs"]


def test_vek_pozice_proti_rozpeti_a_trvale_nejvyssi_skupina(ws):
    out = cr.build_age(_age_rates(ws, [2022, 2023, 2024, 2025]), {})
    ari = out["indicators"]["ari"]
    g = ari["groups"][0]
    assert (g["min"][1], g["max"][1], g["current"][1]) == (2000.0, 2300.0, 3000.0)
    assert ari["top_group"] == "0-4" and ari["top_position"] == "above"
    assert g["now"] == {"value": 3000.0, "min": 2000.0, "max": 2300.0, "position": "above"}
    assert "nad tímto rozpětím" in ari["comment"]["cs"] and "ve všech týdnech" in ari["comment"]["cs"]
    assert out["past_seasons"] == ["2022/23", "2023/24", "2024/25", "2025/26"] and out["last_week"] == "2026-W41"


def test_vek_komentar_nepise_o_rekordu_ani_o_pricinach(ws):
    out = cr.build_age(_age_rates(ws, [2022, 2023, 2024, 2025]), {"2026-W41": {"public": 0, "school": 2.0}})
    text = out["indicators"]["ari"]["comment"]["cs"].lower()
    assert "sváteční týden (školní prázdniny)" in text
    assert not re.search(r"rekord|histori|epidemi|škol[ay]\b|šíř|krát|násob", text)


# ── Typy a subtypy ───────────────────────────────────────────────────────────

def _types_lab(ws, cur, prev=(58, 229, 3365, 26)):
    """cur/prev: (h1, h3, a_unk, b) za jeden týden sezóny"""
    rows = []
    for week, (h1, h3, au, b) in (("2025-W40", prev), ("2026-W40", (0, 0, 0, 0)), ("2026-W41", cur)):
        rows += [(week, "Influenza", "total", "detections", h1 + h3 + au + b),
                 (week, "Influenza", "total", "tests", 5000),
                 (week, "Influenza A", "A(H1)pdm09", "detections", h1), (week, "Influenza A", "A(H3)", "detections", h3),
                 (week, "Influenza A", "A (unknown)", "detections", au),
                 (week, "Influenza B", "B (unknown)", "detections", b)]
    return _lab(ws, rows)


def test_subtyp_prevazuje_jen_pri_dostatku_vzorku(ws):
    out = cr.build_types(_types_lab(ws, (4, 0, 22, 2)))
    assert out["lead"] is None and out["season_totals"][-1]["share_h1"] is None
    assert "nelze určit, který subtyp převažuje" in out["comment"]["cs"]
    assert "převažuje A" not in out["comment"]["cs"]


def test_subtyp_prevazuje_od_sedesati_procent(ws):
    out = cr.build_types(_types_lab(ws, (10, 40, 200, 5)))
    assert out["lead"] == "h3" and "převažuje A(H3) (80 %)" in out["comment"]["cs"]
    out = cr.build_types(_types_lab(ws, (22, 28, 200, 5)))     # 56 % — pod hranicí
    assert out["lead"] is None and "žádný subtyp mezi nimi výrazně nepřevažuje" in out["comment"]["cs"]


def test_typy_soucty_po_sezonach_a_cesky_plural(ws):
    out = cr.build_types(_types_lab(ws, (4, 0, 22, 2)))
    prev, cur = out["season_totals"][-2:]
    assert (prev["season"], prev["a_total"], prev["a_subtyped"], prev["share_h3"]) == ("2025/26", 3652, 287, 79.8)
    assert (cur["a_total"], cur["b"], cur["complete"]) == (26, 2, False)
    assert "Od 28.\u00a0září do 11.\u00a0října laboratoře nahlásily 26 záchytů chřipky A a 2 záchyty chřipky B" in out["comment"]["cs"]
    assert "u 287 z 3 652 záchytů chřipky A (7,9 %); z nich 229 bylo A(H3)" in out["comment"]["cs"]
    assert out["weekly"]["2026/27"]["provisional"] == ["2026-W40", "2026-W41"]


def test_neznamy_subtyp_se_zapocte_podle_typu(ws, capsys):
    lab = _lab(ws, [("2026-W40", "Influenza", "total", "detections", 3), ("2026-W40", "Influenza", "total", "tests", 500),
                    ("2026-W40", "Influenza A", "A(H5)", "detections", 3)])
    out = cr.build_types(lab)
    assert out["season_totals"][-1]["a_unk"] == 3 and "neznámé subtypy" in capsys.readouterr().out


def test_komentare_drzi_delku_a_zakazana_slova(ws):
    """Tvrdý limit 80 slov a slova, která do těchhle textů nepatří."""
    rates, lab = _overview_inputs(ws)
    hol = _holidays(ws, [(2026, 44, 1, 2.0), (2026, 45, 0, 0)])
    texts = [cr.build_overview(rates, lab, hol, MEM)["comment"]["cs"],
             cr.build_types(_types_lab(ws, (10, 40, 200, 5)))["comment"]["cs"],
             cr.build_age(_age_rates(ws, [2022, 2023, 2024, 2025]), {})["indicators"]["ari"]["comment"]["cs"]]
    for t in texts:
        assert len(t.split()) <= 80, t
        assert not re.search(r"nakažen|nemocných|v populaci|vln[ay]\b|kmen|mutac|vakcín|očkov|doporuč|naštěstí|alarm", t.lower()), t
        assert not re.search(r"\bKT\b|\d{4}-W\d\d", t), t          # žádná čísla týdnů v textu pro čtenáře


def test_main_zapise_tri_soubory(ws, monkeypatch):
    _overview_inputs(ws)                      # laboratorní část
    _age_rates(ws, [2025])                    # ARI včetně věkových skupin (přepíše rates.csv)
    with open(ws / "rates.csv", "a", encoding="utf-8") as fh:
        fh.write("2026-W40,ILI,total,15.1\n2026-W41,ILI,total,18.1\n")
    _holidays(ws, [(2026, 45, 0, 0)])
    assert cr.main() == 0
    for name in ("resp_overview", "resp_age", "flu_types"):
        obj = json.loads((ws / "out" / f"{name}.json").read_text(encoding="utf-8"))
        assert obj["generated_at"] and obj.get("meta", {}).get("chart_id") == name


def test_limit_delky_plati_pro_delsi_z_obou_jazyku():
    """Čeština se vejde, angličtina ne → nepovinná věta vypadne z obou verzí."""
    from facts import Facts
    f = Facts(max_words=8)
    f.fact("a", "measurement", "Krátce.", "Short.", required=True)
    f.fact("b", "measurement", "Navíc.", "Extra.")
    f.say("Česky tři slova.", "The English version takes quite a few more words.", ["a"])
    f.say("Navíc.", "Extra.", ["b"], optional=True)
    out = f.block()["comment"]
    assert out["cs"] == "Česky tři slova." and "Extra" not in out["en"]

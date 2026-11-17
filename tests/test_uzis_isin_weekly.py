"""Týdenní souhrn ÚZIS (scrapers/uzis_isin_weekly.py) — čtení XLSX a stahování.

Sešit ze zdroje je stavěný pro oči, ne pro stroj: roky leží vedle sebe, mezi
sloupci jsou mezery, uvnitř bloku bývá prázdný řádek. Testy staví malý sešit se
stejnými zvláštnostmi a hlídají, že se čísla přiřadí správnému roku, týdnu a
věkové skupině — a že se rozbitý sešit ohlásí, místo aby dal tiše špatná čísla.
"""
import io
import json
import zipfile
from datetime import date

import pandas as pd
import pytest

openpyxl = pytest.importorskip("openpyxl")

from scrapers import uzis_isin_weekly as u  # noqa: E402

AGES = ["0 let\n0 years", "1 až 4 roky\n1 to 4 years"]


def _sheet(ws, region, years, stamp="15.11.2026 05:34", gap_row=True):
    """
    Jeden list. `years` = {rok: [(isin0, ews0, isin1, ews1), …]} — jeden prvek na týden.
    Sloupce bloku: ISO_WEEK, datum, věk 0 (ISIN, EWS), MEZERA, věk 1 (ISIN, EWS),
    Celkem (ISIN, EWS), MEZERA před dalším blokem.
    """
    ws.cell(1, 1, "Infekce - monitoring nemocí")
    ws.cell(2, 1, "Zpracováno dne: 16.11.2026 03:30")
    ws.cell(3, 1, f"Stav k datu: {stamp}")
    ws.cell(4, 1, "HEPATITIDA A")
    ws.cell(4, 7, "Acute hepatitis A")
    ws.cell(5, 1, "MKN-10 kódy: B15")
    ws.cell(9, 1, region)
    width = 10
    for b, (year, weeks) in enumerate(years.items()):
        c = 1 + b * width
        ws.cell(10, c, str(year) if b == 0 else year)     # zdroj má první rok jako text, další jako číslo
        ws.cell(11, c, "ISO_WEEK")
        ws.cell(11, c + 1, "Datum/Date")
        cols = {AGES[0]: c + 2, AGES[1]: c + 5, "Celkem / Total": c + 7}
        for label, col in cols.items():
            ws.cell(11, col, label)
            ws.cell(12, col, "ISIN")
            ws.cell(12, col + 1, "EWS")
        row = 13
        for w, (a, a_ews, b_, b_ews) in enumerate(weeks, 1):
            if gap_row and w == 2:
                row += 1                                   # prázdný řádek uvnitř bloku
            ws.cell(row, c, f"{year}-{w:02d}")
            ws.cell(row, c + 1, "01.01.26-07.01.26")
            for col, val in ((c + 2, a), (c + 3, a_ews), (c + 5, b_), (c + 6, b_ews),
                             (c + 7, a + b_), (c + 8, a_ews + b_ews)):
                ws.cell(row, col, val)
            row += 1
        ws.cell(row, c, "Celkem/Total")
        ws.cell(row, c + 1, "01.01.23-31.12.23")           # zastaralý popisek, jako ve zdroji
        ws.cell(row, c + 7, 999)


def workbook(years=None, regions=None, tweak=None) -> bytes:
    years = years or {2026: [(1, 0, 4, 2), (2, 1, 3, 0)], 2025: [(5, 0, 6, 0), (7, 0, 8, 0), (9, 0, 1, 0)]}
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, region in enumerate(regions or list(u.REGION_TO_NUTS3)):
        ws = wb.create_sheet(f"L{i}")
        _sheet(ws, region, years)
        if tweak and i == 0:
            tweak(ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def archive(folders: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Infekce/", "")
        for folder, files in folders.items():
            z.writestr(f"Infekce/{folder}/", "")
            for name, content in files.items():
                z.writestr(f"Infekce/{folder}/{name}", content)
    return buf.getvalue()


def test_cisla_patri_spravnemu_roku_tydnu_a_veku():
    df, info = u.parse_workbook(workbook())
    assert info["diagnoza"] == "B15" and info["diagnoza_nazev_en"] == "Acute hepatitis A"
    assert info["stav_k"] == "2026-11-15T05:34" and info["zpracovano"] == "2026-11-16T03:30"
    assert set(df["kraj_kod"]) == set(u.REGION_TO_NUTS3.values())
    cz = df[df["kraj_kod"] == "CZ"].set_index(["rok", "tyden", "vek"])
    # mezera ve sloupcích i prázdný řádek uvnitř bloku se přeskočí
    assert cz.loc[(2026, 1, "1 až 4 roky"), "pocet"] == 4 and cz.loc[(2026, 1, "1 až 4 roky"), "pocet_ews"] == 2
    assert cz.loc[(2026, 2, "0 let"), "pocet"] == 2 and cz.loc[(2026, 2, "0 let"), "pocet_ews"] == 1
    assert cz.loc[(2025, 3, "Celkem"), "pocet"] == 10
    # řádek „Celkem/Total“ pod týdny se nečte jako týden
    assert len(cz.loc[2026]) == 2 * 3 and len(cz.loc[2025]) == 3 * 3


@pytest.mark.parametrize("tweak, message", [
    (lambda ws: ws.cell(13, 8, 77), "věkové skupiny"),          # Celkem nesouhlasí se součtem věku
    (lambda ws: ws.cell(13, 4, 9), "EWS"),                       # víc záznamů z EWS než všech
    (lambda ws: ws.cell(13, 1, "2026-03"), "čekal jsem týden"),  # týdny nejdou po sobě
    (lambda ws: ws.cell(13, 3, "x"), "místo počtu"),
    (lambda ws: ws.cell(9, 1, "Kraj Neznámý"), "neznámé území"),
    (lambda ws: ws.cell(11, 1, "TYDEN"), "si neodpovídají"),     # blok běžícího roku by tiše zmizel
])
def test_rozbity_sesit_se_ohlasi(tweak, message):
    with pytest.raises(ValueError, match=message):
        u.parse_workbook(workbook(tweak=tweak))


def test_chybejici_kraj_se_ohlasi():
    with pytest.raises(ValueError, match="chybí území"):
        u.parse_workbook(workbook(regions=list(u.REGION_TO_NUTS3)[:-1]))


def test_bere_se_nejnovejsi_slozka_ktera_ma_soubory():
    names = ["Infekce/", "Infekce/2026-11-16/", "Infekce/2026-11-16/a.xlsx", "Infekce/2026-11-09/b.xlsx",
             "Infekce/2026-11-23/", "Infekce/2027-01-04/"]     # budoucí složky jsou založené dopředu a prázdné
    assert u.latest_folder(names) == ("2026-11-16", ["Infekce/2026-11-16/a.xlsx"])
    with pytest.raises(ValueError, match="žádná složka"):
        u.latest_folder(["Infekce/", "Infekce/2026-11-23/"])


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def test_download_zapise_csv_a_stav_a_podruhe_nestahuje(tmp_path, monkeypatch):
    calls = []
    content = archive({"2026-11-09": {"stary.xlsx": workbook()}, "2026-11-16": {"novy.xlsx": workbook()},
                       "2026-11-23": {}})

    def get(url, timeout):
        calls.append(url)
        return _Response(content)
    monkeypatch.setattr(u.requests, "get", get)

    files = u.download(tmp_path, today=date(2026, 11, 17))
    state = json.loads((tmp_path / u.STATE_NAME).read_text(encoding="utf-8"))
    assert state["slozka"] == "2026-11-16" and state["stav_k"] == "2026-11-15T05:34"
    assert state["diagnozy"] == {"B15": {"cs": "HEPATITIDA A", "en": "Acute hepatitis A"}}
    assert (tmp_path / u.CSV_NAME).read_text(encoding="utf-8").startswith(",".join(u.COLUMNS))
    assert len(files) == 2 and len(calls) == 1

    # týž týden: 32 MB se znovu netahá
    u.download(tmp_path, today=date(2026, 11, 20))
    assert len(calls) == 1
    # další pondělí: zkusí se nová složka
    u.download(tmp_path, today=date(2026, 11, 23))
    assert len(calls) == 2


def test_zdroj_ktery_se_prestal_obnovovat_selze_a_nechá_stara_data(tmp_path, monkeypatch):
    content = archive({"2026-09-21": {"a.xlsx": workbook()}})
    monkeypatch.setattr(u.requests, "get", lambda url, timeout: _Response(content))
    with pytest.raises(ValueError, match="neobnovuje"):
        u.download(tmp_path, today=date(2026, 11, 17))
    assert not (tmp_path / u.CSV_NAME).exists()


def test_rok_ktery_zdroj_uz_nenese_zustane_z_minuleho_stazeni(tmp_path, monkeypatch):
    # minulé stažení: 2024–2025; nové: 2025–2026 → 2024 zůstane, 2025 se vezme ze zdroje
    old = archive({"2026-11-09": {"a.xlsx": workbook(years={2025: [(1, 0, 1, 0)], 2024: [(3, 0, 3, 0)]})}})
    new = archive({"2026-11-16": {"a.xlsx": workbook(years={2026: [(1, 0, 4, 2)], 2025: [(5, 0, 6, 0)]})}})
    monkeypatch.setattr(u.requests, "get", lambda url, timeout: _Response(old))
    u.download(tmp_path, today=date(2026, 11, 10))
    monkeypatch.setattr(u.requests, "get", lambda url, timeout: _Response(new))
    u.download(tmp_path, today=date(2026, 11, 17))

    df = pd.read_csv(tmp_path / u.CSV_NAME)
    assert list(df.columns) == u.COLUMNS
    cz = df[(df["kraj_kod"] == "CZ") & (df["vek"] == "Celkem")].set_index("rok")["pocet"]
    assert cz.to_dict() == {2024: 6, 2025: 11, 2026: 5}
    assert not df.duplicated(["diagnoza", "kraj_kod", "rok", "tyden", "vek"]).any()


def test_minule_csv_s_jinymi_sloupci_se_nepouzije(tmp_path, monkeypatch):
    (tmp_path / u.CSV_NAME).write_text("diagnoza,rok,pocet\nB15,2020,5\n", encoding="utf-8")
    content = archive({"2026-11-16": {"a.xlsx": workbook()}})
    monkeypatch.setattr(u.requests, "get", lambda url, timeout: _Response(content))
    u.download(tmp_path, today=date(2026, 11, 17))
    assert set(pd.read_csv(tmp_path / u.CSV_NAME)["rok"]) == {2025, 2026}

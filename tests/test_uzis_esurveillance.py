"""
Denní e-surveillance ÚZIS: scraper, generátor grafů a řádky pro databázi.

Bez sítě. Excelové soubory se tu staví ve stejném tvaru, jaký má zdroj (ověřeno na
složce 2026-11-25): text nad hlavičkou, data jako „DD.MM.RRRR“, bloky sloupců
vedle sebe s mezerami, list na kraj. Čísla jsou vymyšlená a jednoduchá, aby šla
v testech přepočítat z hlavy.
"""
import io
import json
from datetime import date, timedelta

import openpyxl
import pandas as pd
import pytest

import compute_esurv
import last_checked
import load_to_db
from scrapers import uzis_esurveillance as u

LAST = date(2026, 11, 24)                       # „stav k datu“
N_DAYS = 40
DAYS = [LAST - timedelta(days=i) for i in range(N_DAYS)]            # jako zdroj: od nejnovějšího
AGE_LABELS = ["0 let", "1 až 4 roky", "5 až 9 let", "10 až 14 let", "15 až 19 let", "20 až 24 let",
              "25 až 34 let", "35 až 44 let", "45 až 54 let", "55 až 64 let", "65 až 74 let", "75 a více let"]
REGION_NAMES = [n for n in u.REGION_TO_NUTS3 if n not in ("ČR", "Česká republika", "Neznámé bydliště")]


def cz(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def xlsx(sheets: dict) -> bytes:
    """{jméno listu: [řádky]} → obsah souboru; vždy s úvodním listem jako ve zdroji."""
    wb = openpyxl.Workbook()
    wb.active.title = "Úvod"
    wb.active.append(["Reporty jsou vytvářeny v rámci projektu SCOPE IS"])
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def head(title: str) -> list:
    """Devět řádků nad hlavičkou tabulky (r0–r8 bez nadpisů bloků)."""
    return [[title], [], [], ["Reporty jsou vytvářeny v rámci projektu SCOPE IS"],
            ["Zpracováno dne: 25.11.2026 06:00"], [], [f"Stav k datu: {cz(LAST)}"], []]


def cases_by_age(day_index: int) -> list:
    return [a + 1 + day_index % 3 for a in range(12)]


def ew05(shift: int = 0, drop_block: str | None = None) -> bytes:
    """`shift` vloží před věkové bloky prázdné sloupce navíc — poloha se má číst z hlavičky."""
    blocks = [b for b in u.EW05_AGE_BLOCKS if b != drop_block]
    sheets = {}
    for sheet in u.SHEET_TO_NUTS3:
        titles, header = [None, "Souhrnné informace"], [f"Datum - {sheet}", *u.EW05_SUMMARY]
        for title in blocks:
            pad = [None] * (1 + shift)
            header += pad + ["Datum"] + ([None] if title == blocks[0] else []) + AGE_LABELS
            titles += [None] * (len(header) - len(AGE_LABELS) - len(titles)) + [title]
        rows = head("Denní přehled počtu nákaz a související zátěže nemocnic") + [titles, header]
        for i, d in enumerate(DAYS):
            ages = cases_by_age(i)
            row = [cz(d), sum(ages), 1, 5, 1 if i == 0 else 0, 1 if i == 1 else 0]
            for title in blocks:
                values = {"pripady": ages, "hosp_nove": [0] * 11 + [1], "hosp_aktualne": [0] * 11 + [5],
                          "jip_nove": [1 if i == 0 else 0] + [0] * 11}[u.EW05_AGE_BLOCKS[title]]
                row += [None] * (1 + shift) + [cz(d)] + ([None] if title == blocks[0] else []) + values
            rows.append(row)
        sheets[sheet] = rows
    return xlsx(sheets)


def ehos03() -> bytes:
    sheets = {}
    for sheet, code in u.SHEET_TO_NUTS3.items():
        nuts = "CZ000" if code == "CZ" else code
        rows = head("Přehled počtu hospitalizovaných pacientů") + [
            ["Kraj", None, "Celkové počty hospitalizovaných", None, "Typ péče - nově hospitalizovaní pacienti"],
            ["Datum", "Kraj", *u.EHOS03]]
        rows += [[cz(d), nuts, 1, 5, 1 if i == 0 else 0, 1 if i == 2 else 0, 0, 0, 0, 1 if i == 1 else 0]
                 for i, d in enumerate(DAYS)]
        sheets[f"{sheet} {nuts}"] = rows
    return xlsx(sheets)


def evak01() -> bytes:
    rows = head("Přehled o počtu podaných dávek")[:7] + [
        ["Látky využité pro vakcinaci/imunizaci v dané sezóně - VAXIGRIP; kódy dle SUKL: 0231888"], [],
        ["CELKOVÝ POČET PODANÝCH DÁVEK VAKCÍN v sezóně 2026/2027 (1.9. - 31.8.)"],
        ["Datum", "Hlavní město Praha ", *REGION_NAMES[1:], "Neznámé bydliště", "ČR"]]
    rows += [[cz(d), *([10] * 14), 2, 142] for d in DAYS] + [["Celkem", *([10 * N_DAYS] * 14), 2 * N_DAYS, 142 * N_DAYS]]
    return xlsx({"Kraje": rows})


def evak02(labels: list | None = None) -> bytes:
    labels = labels or AGE_LABELS
    rows = head("Přehled o počtu podaných dávek dle věku")[:7] + [
        ["Látky využité pro vakcinaci/imunizaci v dané sezóně - VAXIGRIP"], [],
        [None, "CELKOVÝ POČET PODANÝCH DÁVEK VAKCÍN dle věku"], ["Datum", *labels, "Celkem"]]
    rows += [[cz(d), *([3] * len(labels)), 3 * len(labels)] for d in DAYS]
    rows += [["Celkem", *([3 * N_DAYS] * len(labels)), 3 * len(labels) * N_DAYS]]
    return xlsx({"Věky": rows})


def ew07(duplicate: bool = True, clash: bool = False) -> bytes:
    rows = head("Infekce potvrzené laboratořemi")[:4] + [[], ["Zpracováno dne: 25.11.2026 06:00"],
                                                         [f"Stav k datu: {cz(LAST)}"], [],
                                                         ["Celkový počet hlásících laboratoří do EWS: 74 (73,3 %)"], [],
                                                         ["Datum odběru vzorku", *u.EW07]]
    data = [[cz(d), 101, 74, 3, 0, 4, 4] for d in DAYS]
    if duplicate:
        data.insert(3, list(data[3]))                 # zdroj některé dny opakuje
    if clash:
        data.insert(5, [data[5][0], 101, 74, 3, 0, 9, 9])
    return xlsx({"Data": rows + data, "Seznam laboratoří": [["IČO", "Název laboratoře"]]})


def ew03() -> bytes:
    def table(extra: list, lines: list) -> list:
        return [["Epidemiologické parametry"], [], [], [],
                [f"Zpracováno dne: 25.11.2026 06:00; Stav k datu: {cz(LAST)}"], [],
                [None] * (3 + len(extra)) + ["Hlášené případy nákazy (hlášení lékařů)"],
                ["K datu", "NUTS", "Kraj", *extra, *u.EW03]] + lines
    kraje = [[cz(LAST), code, name, *[float(i + 1)] * 13] for i, (name, code) in
             enumerate((n, u.REGION_TO_NUTS3[n]) for n in REGION_NAMES)]
    kraje += [[cz(LAST), None, "Česká republika", *[7.5] * 13], [cz(LAST), "CZ099", None, *[0] * 13]]
    okresy = [[cz(LAST), "CZ010", "Hlavní město Praha", "CZ0100", "Praha", *[1.0] * 13],
              [cz(LAST), "CZ020", "Středočeský kraj", "CZ0201", "Benešov", *[2.0] * 13]]
    return xlsx({"Kraj": table([], kraje), "Okres": table(["LAU", "Okres"], okresy)})


def ew04() -> bytes:
    rows = head("Nákazy a vakcinace")[:7] + [[], ["Datum", "Počet nových nahlášených případů", "Sezóna",
                                                  *[c for c in u.EW04 if c != "Počet nových nahlášených případů"]]]
    rows += [[cz(d), 10, "2026/2027", 8, 2, 1, 1, 0, 0, 0, 0] for d in DAYS]
    return xlsx({"PREHLED-nakazy, ockováni": rows})


@pytest.fixture(scope="module")
def folder() -> dict:
    """Úplná denní složka: 24 souborů pro čtyři nemoci."""
    files = {}
    for kind, _, diseases in u.WANTED:
        for d in diseases:
            files[(kind, d)] = {"EW05": ew05, "EW03": ew03, "EW07": ew07, "EW04": ew04, "eHos03": ehos03,
                                "eVak01": evak01, "eVak02": evak02}[kind]()
    return files


@pytest.fixture(scope="module")
def parsed(folder):
    return u.parse_folder(folder)


# ── čtení souborů ────────────────────────────────────────────────────────────

def test_cela_slozka_da_sest_tabulek_a_stav(parsed):
    frames, state = parsed
    assert set(frames) == set(u.CSV)
    assert state["stav_k"] == "2026-11-24" and state["zpracovano"] == "2026-11-25T06:00"
    assert state["rozsah"] == {"od": DAYS[-1].isoformat(), "do": "2026-11-24"}
    assert "VAXIGRIP" in state["vakciny"]["chripka"]
    d = frames["denni"]
    # 4 nemoci × 15 území × 40 dní × (celkem + 12 věkových skupin)
    assert len(d) == 4 * 15 * N_DAYS * 13
    assert set(d["nemoc"]) == {"chripka", "covid19", "rsv", "ari_ili"}
    assert set(d["kraj_kod"]) == set(u.SHEET_TO_NUTS3.values())


def test_ew05_cte_souhrn_i_vekove_bloky(parsed):
    d = parsed[0]["denni"]
    day = d[(d["nemoc"] == "chripka") & (d["kraj_kod"] == "CZ") & (d["datum"] == "2026-11-24")].set_index("vek")
    assert day.loc["celkem", "pripady"] == sum(cases_by_age(0)) == 78
    assert day.loc["celkem", ["hosp_nove", "hosp_aktualne", "jip_nove", "umrti_hosp"]].tolist() == [1, 5, 1, 0]
    assert day.loc["0", "pripady"] == 1 and day.loc["75+", "pripady"] == 12
    assert day.loc["75+", "hosp_aktualne"] == 5 and day.loc["0", "jip_nove"] == 1
    assert pd.isna(day.loc["0", "umrti_hosp"])          # úmrtí zdroj podle věku neuvádí
    # součet věkových skupin = celkem, jako ve zdroji
    ages = d[(d["vek"] != "celkem")].groupby(["nemoc", "kraj_kod", "datum"])["pripady"].sum()
    total = d[d["vek"] == "celkem"].set_index(["nemoc", "kraj_kod", "datum"])["pripady"]
    assert (ages == total.loc[ages.index]).all()


def test_ew05_poloha_sloupcu_se_cte_z_hlavicky():
    """Zdroj má mezi bloky prázdné sloupce a jejich počet se může změnit."""
    a, _ = u.parse_ew05(ew05(), "chripka")
    b, _ = u.parse_ew05(ew05(shift=2), "chripka")
    pd.testing.assert_frame_equal(a, b)


def test_ew05_bez_vekoveho_bloku_nebo_listu_selze_nahlas():
    with pytest.raises(ValueError, match="chybí věkové bloky"):
        u.parse_ew05(ew05(drop_block="Hospitalizovaní na JIP nově dle věkových kategorií"), "chripka")
    wb = openpyxl.load_workbook(io.BytesIO(ew05()))
    del wb["JMK"]
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="čekal jsem listy ČR a 14 krajů"):
        u.parse_ew05(buf.getvalue(), "chripka")


def test_ehos03_ma_typ_pece_a_kod_cele_republiky(parsed):
    n = parsed[0]["nemocnice"]
    assert len(n) == 4 * 15 * N_DAYS
    row = n[(n["nemoc"] == "rsv") & (n["kraj_kod"] == "CZ") & (n["datum"] == DAYS[2].isoformat())].iloc[0]
    assert (row["hosp_nove"], row["hosp_aktualne"], row["jip"], row["aro"], row["umrti_hosp"]) == (1, 5, 0, 1, 0)
    assert "CZ000" not in set(n["kraj_kod"])


def test_ockovani_podle_kraje_i_veku_bez_radku_celkem(parsed):
    v = parsed[0]["ockovani"]
    flu = v[v["nemoc"] == "chripka"]
    assert set(flu[flu["rozmer"] == "kraj"]["kategorie"]) == set(u.SHEET_TO_NUTS3.values()) | {"neznamy"}
    assert set(flu[flu["rozmer"] == "vek"]["kategorie"]) == set(u.AGE_KEYS) | {"celkem"}
    assert set(flu["datum"]) == {d.isoformat() for d in DAYS}        # řádek „Celkem“ dole se nečte
    assert flu[(flu["rozmer"] == "vek") & (flu["kategorie"] == "celkem")]["davky"].sum() == 36 * N_DAYS
    assert "ari_ili" not in set(v["nemoc"])


def test_ockovani_proti_rsv_ma_jine_vekove_skupiny():
    labels = ["0 měsíců", "1 měsíc", "2 měsíce", "11 měsíců", "1 rok", "2 až 4 roky", "15 až 18 let", "65 let a více"]
    df, _ = u.parse_evak(evak01(), evak02(labels), "rsv")
    assert set(df[df["rozmer"] == "vek"]["kategorie"]) == {"0m", "1m", "2m", "11m", "1", "2-4", "15-18", "65+", "celkem"}
    with pytest.raises(ValueError, match="neznámý sloupec"):
        u.parse_evak(evak01(), evak02(["0 let", "dospělí"]), "rsv")


@pytest.mark.parametrize("label, key", [
    ("0 let", "0"), ("1 až 4 roky", "1-4"), ("75 a více let", "75+"), ("65 let a více", "65+"),
    ("3 měsíce", "3m"), ("1 rok", "1"), ("Celkem", "celkem"), ("Datum", None), ("ČR", None),
])
def test_vekova_skupina_z_textu(label, key):
    assert u.age_key(label) == key


def test_ew07_shodne_radky_slouci_a_rozpor_ohlasi(parsed, folder):
    lab = parsed[0]["laboratore"]
    assert len(lab) == 4 * N_DAYS                       # zdvojený den je tam jednou
    assert lab["pozitivni"].eq(4).all() and lab["laboratori_ews"].eq(74).all()
    broken = {**folder, ("EW07", "RSV"): ew07(clash=True)}
    with pytest.raises(ValueError, match="stejný klíč .* a různá čísla"):
        u.parse_folder(broken)


def test_ew03_kraje_okresy_a_radek_republiky_bez_kodu(parsed):
    t = parsed[0]["uzemi"]
    flu = t[t["nemoc"] == "chripka"]
    assert set(flu[flu["uroven"] == "kraj"]["nuts"]) == set(u.SHEET_TO_NUTS3.values()) | {"CZ099"}
    assert flu[(flu["uroven"] == "kraj") & (flu["nuts"] == "CZ")]["pripady_7d_100k"].iloc[0] == 7.5
    okres = flu[flu["uroven"] == "okres"]
    assert okres["lau"].tolist() == ["CZ0100", "CZ0201"] and okres["okres"].tolist() == ["Praha", "Benešov"]


def test_neuplna_slozka_je_chyba(folder):
    incomplete = {k: v for k, v in folder.items() if k != ("eHos03", "RSV")}
    with pytest.raises(ValueError, match="ve složce chybí soubory"):
        u.parse_folder(incomplete)


def test_ktere_soubory_se_ze_slozky_berou():
    assert u.classify("EW05_Chripka_Nakazy_a_hospitalizace-denni_data_20261125.xlsx") == ("EW05", "Chripka")
    assert u.classify("eVak02_COVID-19_vekove_kategorie_20261125.xlsx") == ("eVak02", "COVID-19")
    assert u.classify("EW04_RSV_Nakazy_vakcinace-denni_data_20261125.xlsx") is None      # jen chřipka a covid
    assert u.classify("eHos02_Chripka_Prehled_nemocnic_a_kraju_20261125.xlsx") is None   # po nemocnicích se nebere
    assert u.classify("eVak01_ARIILI_davky_prehled_kraje_20261125.xlsx") is None
    assert u.classify("EW01_Chripka_nove_pripady_shrnuti_20261125.docx") is None


# ── stahování ────────────────────────────────────────────────────────────────

@pytest.fixture
def share(monkeypatch, folder):
    """Sdílená složka bez sítě: výpis kořene a obsah denní složky."""
    calls = {"fetch": 0}
    state = {"listing": [("/2026-11-24/", True), ("/2026-11-25/", True), ("/readme.txt", False)]}

    def propfind(path):
        assert path == "/", "výpis podsložek dělá _fetch_folder, ten je tu nahrazený"
        return state["listing"]

    def fetch(day):
        calls["fetch"] += 1
        return folder

    monkeypatch.setattr(u, "_propfind", propfind)
    monkeypatch.setattr(u, "_fetch_folder", fetch)
    return calls, state


def test_stazeni_zapise_tabulky_a_stav(share, tmp_path):
    calls, _ = share
    files = u.download(tmp_path, today=date(2026, 11, 26))
    assert calls["fetch"] == 1 and len(files) == 7
    state = json.loads((tmp_path / "esurv_stav.json").read_text(encoding="utf-8"))
    assert state["slozka"] == "2026-11-25" and state["stav_k"] == "2026-11-24" and state["fetched_at"].endswith("Z")
    assert len(pd.read_csv(tmp_path / "esurv_denni.csv")) == 4 * 15 * N_DAYS * 13
    assert u.download.source_checked is True


def test_stejna_slozka_se_nestahuje_ale_zdroj_je_overeny(share, tmp_path):
    """Výpis kořene je dotaz na zdroj — „naposledy ověřeno“ se smí posunout i bez nových dat."""
    calls, _ = share
    u.download(tmp_path, today=date(2026, 11, 26))
    before = (tmp_path / "esurv_denni.csv").stat().st_mtime_ns
    u.download(tmp_path, today=date(2026, 11, 26))
    assert calls["fetch"] == 1
    assert (tmp_path / "esurv_denni.csv").stat().st_mtime_ns == before
    assert u.download.source_checked is True


def test_nova_slozka_se_stahne(share, tmp_path):
    calls, state = share
    u.download(tmp_path, today=date(2026, 11, 26))
    state["listing"] = state["listing"] + [("/2026-11-26/", True)]
    u.download(tmp_path, today=date(2026, 11, 27))
    assert calls["fetch"] == 2
    assert json.loads((tmp_path / "esurv_stav.json").read_text(encoding="utf-8"))["slozka"] == "2026-11-26"


def test_zdroj_ktery_se_neobnovuje_selze_nahlas(share, tmp_path):
    with pytest.raises(ValueError, match="zdroj se neobnovuje"):
        u.download(tmp_path, today=date(2026, 12, 20))
    assert not (tmp_path / "esurv_stav.json").exists()


def test_zmenena_struktura_uloziste_selze_nahlas():
    with pytest.raises(ValueError, match="nejsou denní složky"):
        u.newest_folder([("/Infekce/", True), ("/readme.txt", False)])
    assert u.newest_folder([("/2026-09-01/", True), ("/2026-11-25/", True), ("/2026-12-01", False)]) == "2026-11-25"


# ── generátor ────────────────────────────────────────────────────────────────

@pytest.fixture
def workdir(parsed, tmp_path, monkeypatch):
    """$DATA_DIR se staženou e-surveillance a populací, prázdný $OUTPUT_DIR."""
    frames, state = parsed
    base = tmp_path / "data" / "uzis_esurv"
    base.mkdir(parents=True)
    for key, name in u.CSV.items():
        frames[key].to_csv(base / name, index=False)
    (base / "esurv_stav.json").write_text(json.dumps(
        {"slozka": "2026-11-25", **state, "fetched_at": "2026-11-26T05:10:00Z"}), encoding="utf-8")
    pop = pd.DataFrame({"rok": 2025, "vek": range(0, 101), "pocet": 100000})
    (tmp_path / "data" / "csu").mkdir()
    pop.to_csv(tmp_path / "data" / "csu" / "population_age.csv", index=False)
    for module in (compute_esurv, load_to_db):
        monkeypatch.setattr(module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(compute_esurv, "ESURV_DIR", base)
    monkeypatch.setattr(compute_esurv, "POP_AGE_FILE", tmp_path / "data" / "csu" / "population_age.csv")
    monkeypatch.setattr(compute_esurv, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr("sys.argv", ["compute_esurv.py"])
    # Bez svátků, ať testy nezávisí na kalendáři; svátky mají vlastní test.
    monkeypatch.setattr(compute_esurv, "load_holidays", lambda: {})
    return tmp_path


def read(workdir, name):
    return json.loads((workdir / "out" / f"{name}.json").read_text(encoding="utf-8"))


def test_generator_zapise_patnact_souboru_s_metadaty(workdir):
    assert compute_esurv.main() == 0
    names = {p.stem for p in (workdir / "out").glob("*.json")}
    assert names == {"esurv_summary", "esurv_cases", "esurv_cases_age", "esurv_hosp_current", "esurv_hosp_new",
                     "esurv_hosp_icu", "esurv_hosp_deaths", "esurv_hosp_age", "esurv_vaccination",
                     "esurv_vaccination_age", "esurv_lab", "esurv_map_chripka", "esurv_map_covid19",
                     "esurv_map_rsv", "esurv_map_ari_ili"}
    for name in names:
        meta = read(workdir, name)["meta"]
        assert meta["source_ids"][0] == "uzis-esurveillance" and meta["title"]["en"]
        # zdroj licenci neuvádí → graf nesmí vyjít pod CC BY
        assert meta["licence"]["id"] == "source-terms", name
    assert set(last_checked.load_state()["charts"]) == names


def test_sedmidenni_soucet_obsahuje_kazdy_den_tydne_jednou(workdir):
    compute_esurv.main()
    c = read(workdir, "esurv_cases")
    assert c["labels"][-1] == "2026-11-24" and len(c["labels"]) == N_DAYS - 6       # prvních 6 dní nemá celý týden
    assert [d["label"] for d in c["datasets"]] == ["Chřipka", "COVID-19", "RSV", "ARI/ILI celkem"]
    assert c["datasets"][0]["label_en"] == "Influenza"
    # denní případy jsou 78, 90, 102 podle dne % 3; sedm po sobě jdoucích dní od nejnovějšího: 3×78 + 2×90 + 2×102
    assert c["datasets"][0]["data"][-1] == 3 * 78 + 2 * 90 + 2 * 102
    assert read(workdir, "esurv_hosp_new")["datasets"][0]["data"][-1] == 7
    assert read(workdir, "esurv_hosp_current")["datasets"][1]["data"][-1] == 5       # stav, ne součet
    assert read(workdir, "esurv_hosp_icu")["datasets"][0]["data"][-1] == 2          # JIP den 0 + ARO den 2
    assert read(workdir, "esurv_hosp_deaths")["datasets"][0]["data"][-1] == 1


def test_souhrn_ma_dlazdice_datum_a_komentare_z_faktu(workdir):
    compute_esurv.main()
    s = read(workdir, "esurv_summary")
    assert s["posledni_datum"] == "2026-11-24" and s["slozka"] == "2026-11-25"
    # poslední ÚPLNÝ týden je 16.–22. 11.; 23.–24. 11. je běžící týden a nepočítá se
    assert (s["tyden"], s["tyden_predchozi"]) == ("2026-W47", "2026-W46")
    flu = s["nemoci"]["chripka"]
    # den i má 78 + 12 × (i % 3) případů; W47 = dny 2–8 od 24. 11., W46 = dny 9–15
    assert (flu["pripady_tyden"], flu["pripady_tyden_predchozi"]) == (642, 618)
    assert flu["hosp_nove_tyden"] == 7 and s["hosp_aktualne_chripka"] == 5
    assert set(s["blocks"]) == {"cases", "hospital", "vaccination"}
    cases = s["blocks"]["cases"]
    assert cases["period"]["cs"] == "Týden 16.–22.\u00a0listopadu\u00a02026"
    assert "chřipka 642" in cases["comment"]["cs"] and "influenza 642" in cases["comment"]["en"]
    assert "o 4\u00a0% více než v předchozím týdnu (618)" in cases["comment"]["cs"]
    assert {f["id"] for f in cases["facts"]["facts"]} >= {"cases", "change_flu"}
    assert "s chřipkou 5" in s["blocks"]["hospital"]["comment"]["cs"]
    assert s["ockovani"]["chripka"] == {"davky_celkem": 36 * N_DAYS, "davky_7d": 36 * 7}


def test_svatek_ve_srovnavanem_tydnu_zmenu_nepise(workdir, monkeypatch):
    """17. 11. 2026: klouzavých sedm dní by ukázalo falešný skok — týden se svátkem se nesrovnává."""
    holidays = {"2026-W46": {"public": 0, "school": 0.0}, "2026-W47": {"public": 1, "school": 0.0}}
    monkeypatch.setattr(compute_esurv, "load_holidays", lambda: holidays)
    compute_esurv.main()
    cases = read(workdir, "esurv_summary")["blocks"]["cases"]
    assert "státní svátek" in cases["comment"]["cs"] and "proto týdny nesrovnáváme" in cases["comment"]["cs"]
    assert "více než" not in cases["comment"]["cs"] and "méně než" not in cases["comment"]["cs"]
    assert "caveat_holiday" in cases["facts"]["required"]


def test_tyden_se_pocita_jen_uplny():
    wide = pd.DataFrame({"chripka": [1.0] * 10},
                        index=[(date(2026, 11, 9) + timedelta(days=i)).isoformat() for i in range(10)])
    out = compute_esurv.weekly(wide, "2026-11-18")
    assert list(out.index) == ["2026-W46"] and out.loc["2026-W46", "chripka"] == 7


def test_vek_na_sto_tisic_a_davky_na_sto_obyvatel(workdir):
    compute_esurv.main()
    age = read(workdir, "esurv_cases_age")
    assert age["labels"][0] == "0 let" and age["labels"][-1] == "75+ let" and age["labels_en"][1] == "1–4 years"
    flu = age["datasets"][0]
    # skupina 0 let: 7 dní × (1 + den % 3) = 3×1 + 2×2 + 2×3 = 13 případů na 100 000 obyvatel
    assert flu["pocty"][0] == 13 and flu["data"][0] == 13.0
    # 75+: 26 ročníků po 100 000 → 3×12 + 2×13 + 2×14 = 90 případů na 2,6 mil.
    assert flu["pocty"][-1] == 90 and flu["data"][-1] == round(90 / 26, 1)
    hosp = read(workdir, "esurv_hosp_age")
    assert hosp["datasets"][0]["pocty"][-1] == compute_esurv.HOSP_AGE_DAYS and hosp["obdobi"].endswith("2026-11-24")
    vax = read(workdir, "esurv_vaccination_age")
    assert [d["label"] for d in vax["datasets"]] == ["Proti chřipce", "Proti covidu-19"]       # RSV má jiné skupiny
    assert vax["datasets"][0]["data"][0] == round(100 * 3 * N_DAYS / 100000, 1)


def test_mapa_bere_hodnoty_zdroje_a_jen_ctrnact_kraju(workdir):
    compute_esurv.main()
    m = read(workdir, "esurv_map_chripka")
    assert set(m["regions"]) == set(compute_esurv.NUTS3) and m["regions"]["CZ010"] == "1.0"
    assert m["labels"]["CZ064"] == "Jihomoravský" and m["labels_en"]["CZ010"] == "Prague"
    assert m["year"] == "2026-11-24"


def test_ockovani_je_kumulativni_a_laborator_sedmidenni(workdir):
    compute_esurv.main()
    v = read(workdir, "esurv_vaccination")
    assert [d["label"] for d in v["datasets"]] == ["Proti chřipce", "Proti covidu-19", "Proti RSV"]
    assert v["datasets"][0]["data"][0] == 36 and v["datasets"][0]["data"][-1] == 36 * N_DAYS
    assert "VAXIGRIP" in v["vakciny"]["chripka"]
    lab = read(workdir, "esurv_lab")
    assert lab["datasets"][0]["data"][-1] == 28 and lab["laboratore"] == {"celkem": 101, "zapojene_do_ews": 74}


def test_bez_zmeny_vstupu_se_soubory_neprepisuji(workdir, capsys):
    compute_esurv.main()
    before = {p.name: p.stat().st_mtime_ns for p in (workdir / "out").glob("*.json")}
    compute_esurv.main()
    assert {p.name: p.stat().st_mtime_ns for p in (workdir / "out").glob("*.json")} == before
    assert "beze změny" in capsys.readouterr().out


def test_bez_populace_chybi_jen_grafy_podle_veku(workdir):
    (workdir / "data" / "csu" / "population_age.csv").unlink()
    compute_esurv.main()
    names = {p.stem for p in (workdir / "out").glob("*.json")}
    assert "esurv_cases" in names and "esurv_map_rsv" in names
    assert not names & {"esurv_cases_age", "esurv_hosp_age", "esurv_vaccination_age"}


def test_bez_stazenych_dat_generator_nic_nezapise(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compute_esurv, "ESURV_DIR", tmp_path / "nic")
    monkeypatch.setattr(compute_esurv, "OUTPUT_DIR", tmp_path / "out")
    assert compute_esurv.main() == 0
    assert not (tmp_path / "out").exists() and "přeskakuji" in capsys.readouterr().out


# ── řádky pro databázi ───────────────────────────────────────────────────────

def test_radky_pro_databazi(workdir):
    rows, snap = load_to_db.esurveillance_rows()
    assert snap == date(2026, 11, 25)                                   # snímek = denní složka zdroje
    df = pd.DataFrame(rows, columns=["source", "code", "name", "region", "age", "sex", "start", "end", "metric",
                                     "value", "snap"])
    assert set(df["source"]) == {"uzis_esurv"} and set(df["name"]) == {"Chřipka", "COVID-19", "RSV", "ARI/ILI"}
    assert set(df["metric"]) == {"cases", "hospitalizations", "hospitalized", "icu_admissions", "deaths",
                                 "vaccine_doses"}
    assert (df["end"] - df["start"]).eq(timedelta(days=1)).all()
    flu = df[df["name"] == "Chřipka"]
    national = flu[flu["region"].isna() & flu["age"].isna()]
    # celostátní součty jsou úplné včetně nul: úmrtí je 1 jen jeden den ze 40
    deaths = national[national["metric"] == "deaths"]
    assert len(deaths) == N_DAYS and deaths["value"].sum() == 1
    # věk jen za celou ČR, kraje jen celkem; nulové rozpady se nezapisují
    assert flu[flu["region"].notna() & flu["age"].notna() & (flu["metric"] != "vaccine_doses")].empty
    by_age = flu[flu["region"].isna() & flu["age"].notna() & (flu["metric"] == "hospitalizations")]
    assert set(by_age["age"]) == {"75+"} and len(by_age) == N_DAYS
    regions = flu[flu["region"].notna() & (flu["metric"] == "cases")]
    assert regions["region"].nunique() == 14 and len(regions) == 14 * N_DAYS
    # očkování: věk za ČR, kraje bez neznámého bydliště a bez zdvojeného součtu ČR
    vax = flu[flu["metric"] == "vaccine_doses"]
    assert vax[vax["region"].notna()]["region"].nunique() == 14
    assert vax[vax["region"].isna() & vax["age"].isna()]["value"].sum() == 36 * N_DAYS


def test_vlastni_datum_snimku_ma_prednost(workdir):
    _, snap = load_to_db.esurveillance_rows(date(2026, 12, 1))
    assert snap == date(2026, 12, 1)


def test_bez_dat_loader_nic_nenahraje(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(load_to_db, "DATA_DIR", tmp_path)
    assert load_to_db.load_esurveillance(None, dry_run=True) == 0
    assert "přeskakuji" in capsys.readouterr().out

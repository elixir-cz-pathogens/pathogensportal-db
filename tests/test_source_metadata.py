"""
Metadata o zdrojích a jejich napojení na grafy.

Žádný test nechodí na síť — živé zjišťování je ověřené proti skutečným zdrojům
ručně, tady jde o logiku kolem: že se přečte to správné pole, že výpadek zdroje
nikoho neshodí a hlavně že mapa grafů nezaostane za kódem, který grafy vyrábí.
"""
import json
from pathlib import Path

import pytest
import yaml

import chart_meta
import source_metadata as sm

ROOT = Path(__file__).resolve().parent.parent


# ── Registr zdrojů ───────────────────────────────────────────────────────────

def test_registry_je_konzistentni():
    sources = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    ids = [s["id"] for s in sources]
    assert len(ids) == len(set(ids)), "duplicitní id zdroje"

    known = set(sm.DISCOVERY) | {"manual", "manual_note"}
    for s in sources:
        assert s.get("publisher"), f"{s['id']}: chybí vydavatel"
        assert s.get("licence", {}).get("name"), f"{s['id']}: chybí licence (i 'neuvedena' je odpověď)"
        assert s.get("files"), f"{s['id']}: chybí soubory, přes které se dohledá snapshot"
        unknown = set(s.get("discovery") or {}) - known
        assert not unknown, f"{s['id']}: neznámý způsob zjišťování {unknown}"
        # `manual` je tvrzení nás, ne zdroje — bez poznámky by se od strojově
        # zjištěného data nedalo odlišit.
        if "manual" in (s.get("discovery") or {}):
            assert s["discovery"].get("manual_note"), f"{s['id']}: manual bez manual_note"
        # Poznámka k licenci je naše čtení podmínek — bez data ověření se od
        # zastaralé nedá odlišit.
        if s["licence"].get("note"):
            assert s["licence"].get("verified"), f"{s['id']}: licence.note bez licence.verified"
        # `open` je naše čtení podmínek, na kterém stojí licence vlastních výstupů —
        # bez odkazu na podmínky by se nedalo zkontrolovat.
        assert isinstance(s["licence"].get("open", False), bool), f"{s['id']}: licence.open není true/false"
        if s["licence"].get("open"):
            assert s["licence"].get("url"), f"{s['id']}: licence.open bez odkazu na podmínky"


def test_zdroje_s_omezenim_nejsou_oznacene_jako_volne():
    """
    SZÚ má v patičce webu CC BY-NC-ND, WHO povoluje užití jen pro veřejné zdraví,
    ECDC a RespiCast mají vlastní podmínky nebo žádné. Příznak `open` u nich
    smí přibýt jen s písemnou odpovědí zdroje — a pak se změní i tento seznam.
    """
    sources = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    open_ids = {s["id"] for s in sources if s["licence"].get("open")}
    restricted = {"szu-respiratory", "who-fluid-flunet", "ecdc-erviss", "ecdc-respicast", "ecdc-covid"}
    assert not open_ids & restricted, f"označeno jako volné bez podkladu: {sorted(open_ids & restricted)}"


def test_caveaty_odkazuji_na_existujici_zaznamy():
    """Překlep v `methodology` by tiše smazal upozornění, ne vyvolal chybu."""
    sources = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    registry = yaml.safe_load((ROOT / "methodology_changes.yaml").read_text(encoding="utf-8"))
    known = {str(r["id"]) for r in registry}
    for s in sources:
        unknown = set(s.get("methodology") or []) - known
        assert not unknown, f"{s['id']}: {unknown} není v methodology_changes.yaml"


# ── Čtení metadat ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-01-22T09:09:17.692Z", "2026-01-22T09:09:17Z"),   # CSVW, zlomky pryč
    ("Wed, 21 Jan 2026 12:54:54 GMT", "2026-01-21T12:54:54Z"),  # HTTP Last-Modified
    ("2026-09-18T13:41:23Z", "2026-09-18T13:41:23Z"),       # GitHub
    ("2022-10-01", "2022-10-01T00:00:00Z"),                 # ruční datum
    ("nesmysl", None),
    (None, None),
])
def test_datum_se_prevede_na_utc(value, expected):
    assert sm._iso(value) == expected


def test_csvw_vytahne_licenci_a_datum(monkeypatch):
    doc = {
        "dc:title": "Infekční nemoci v ČR",
        "dc:publisher": {"schema:name": "ÚZIS ČR"},
        "dc:license": {"@id": "https://data.gov.cz/podmínky-užití/volný-přístup/"},
        "dc:modified": {"@value": "2026-01-22T09:09:17.692Z"},
        "dcat:keyword": ["ISIN", "EWS"],
        "tableSchema": {"columns": [{"name": "rok"}, {"name": "EWS"}]},
    }
    monkeypatch.setattr(sm, "_get", lambda url, accept=None: type("R", (), {"json": lambda self: doc})())
    out = sm.from_csvw("http://example.test/x.csv-metadata.json")
    assert out["modified_at_publisher"] == "2026-01-22T09:09:17Z"
    assert out["licence_claimed"] == "https://data.gov.cz/podmínky-užití/volný-přístup/"
    assert out["columns"] == ["rok", "EWS"]


def test_nkod_prelozi_periodicitu(monkeypatch):
    doc = {"@graph": [{
        "@type": ["http://www.w3.org/ns/dcat#Dataset"],
        "accrualPeriodicity": "http://publications.europa.eu/resource/authority/frequency/ANNUAL",
    }]}
    monkeypatch.setattr(sm, "_get", lambda url, accept=None: type("R", (), {"json": lambda self: doc})())
    assert sm.from_nkod("http://example.test/ds")["periodicity"] == "annual"


def test_vypadek_zdroje_neshodi_ostatni(monkeypatch, tmp_path):
    """Nedostupný zdroj se zapíše do `errors`; ostatní se doberou normálně."""
    monkeypatch.setattr(sm, "SOURCES_FILE", tmp_path / "sources.yaml")
    monkeypatch.setattr(sm, "METHODOLOGY_FILE", tmp_path / "meth.yaml")
    (tmp_path / "meth.yaml").write_text("[]", encoding="utf-8")
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump([
        {"id": "rozbity", "publisher": "X", "licence": {"name": "n"}, "files": [],
         "discovery": {"http_head": "http://example.test/nic"}},
        {"id": "funkcni", "publisher": "Y", "licence": {"name": "n"}, "files": [],
         "discovery": {"manual": "2024-01-01", "manual_note": "ručně"}},
    ]), encoding="utf-8")

    def boom(url):
        raise ConnectionError("zdroj neodpovídá")
    monkeypatch.setattr(sm, "from_http_head", boom)
    monkeypatch.setitem(sm.DISCOVERY, "http_head", boom)

    out = sm.collect()
    assert "ConnectionError" in out["sources"]["rozbity"]["errors"]["http_head"]
    assert out["sources"]["funkcni"]["modified_at_publisher"] == "2024-01-01T00:00:00Z"
    assert out["sources"]["funkcni"]["modified_source"] == "manual"


def test_caveat_se_bere_z_registru_zmen(monkeypatch, tmp_path):
    """Texty upozornění se nekopírují — jeden zdroj pravdy je registr změn."""
    monkeypatch.setattr(sm, "METHODOLOGY_FILE", tmp_path / "meth.yaml")
    registry = [{"id": "ews-2025", "od": "2025-07", "akce": "break",
                 "popis": "Nový kanál\nhlášení EWS.", "dopad": "3× vyšší počty"}]
    out = sm._caveats(["ews-2025", "neexistuje"], registry)
    assert len(out) == 1, "neznámé id se přeskočí, ne vymyslí"
    assert out[0]["description"] == "Nový kanál hlášení EWS."
    assert out[0]["action"] == "break"


# ── Napojení na grafy ────────────────────────────────────────────────────────

def test_kazdy_generovany_graf_ma_zaznam_v_charts_yaml():
    """
    Pojistka proti rozjetí: nový graf v kódu bez řádku v charts.yaml by šel na
    web bez metadat a nikdo by si toho nevšiml.
    """
    import re
    charts = yaml.safe_load((ROOT / "charts.yaml").read_text(encoding="utf-8"))
    sources = {s["id"] for s in yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))}

    generated = set()
    for line in (ROOT / "scripts" / "generate_json.py").read_text(encoding="utf-8").splitlines():
        m = re.search(r'save\(f?"([a-z_]+)(\{[a-z_]+\})?"', line)
        if m:
            generated.add(m.group(1) + ("*" if m.group(2) else ""))
    # Grafy, které si JSON zapisují samy (mimo save())
    generated |= {"flu_mem", "anomaly_signals"}

    assert generated, "nenašel jsem žádné save() — změnil se tvar volání?"
    for name in generated:
        assert chart_meta.spec_for(name.rstrip("*")) is not None, \
            f"graf '{name}' chybí v charts.yaml"

    for name, spec in charts.items():
        ids = spec["source"] if isinstance(spec["source"], list) else [spec["source"]]
        unknown = set(ids) - sources
        assert not unknown, f"{name}: zdroj {unknown} není v sources.yaml"
        assert spec["unit"] in {"count", "per_100k", "percent", "score"}, \
            f"{name}: neznámá jednotka {spec['unit']}"
        assert spec.get("series_code") in {None, *chart_meta.CODE_SYSTEMS}, \
            f"{name}: neznámý číselník řad {spec.get('series_code')}"


def _chart_names() -> set[str]:
    """Jména grafů z charts.yaml; `isin_group_*` rozepsané na skutečné skupiny."""
    import generate_json as g
    charts = yaml.safe_load((ROOT / "charts.yaml").read_text(encoding="utf-8"))
    names = {n for n in charts if not n.endswith("*")}
    assert "isin_group_*" in charts, "skupiny ISIN už nejsou pod hvězdičkou — uprav rozepsání"
    return names | {f"isin_group_{key}" for key in g.DISEASE_GROUPS} | {"isin_group_other"}


def test_kazdy_graf_ma_nazev_v_obou_jazycich():
    """
    Stejná pojistka jako u charts.yaml: graf bez názvu by šel ven jen se jménem
    souboru a katalog by ho neměl jak popsat.
    """
    import re
    titles = yaml.safe_load((ROOT / "curated" / "chart_titles.yaml").read_text(encoding="utf-8"))
    names = _chart_names()

    assert not names - set(titles), f"chybí název v chart_titles.yaml: {sorted(names - set(titles))}"
    assert not set(titles) - names, f"název pro graf, který neexistuje: {sorted(set(titles) - names)}"
    for name, title in titles.items():
        for lang in ("cs", "en"):
            text = title.get(lang)
            assert text and text.strip() == text, f"{name}: chybí nebo je neořezaný název ({lang})"
            # Soubor se přegenerovává, název ne — období patří do meta.period.
            assert not re.search(r"\b(19|20)\d{2}\b", text), \
                f"{name} ({lang}): rok v názvu zastará, období patří do meta.period"
        assert title["cs"] != title["en"], f"{name}: anglický název je opsaný český"


def test_meta_nese_jednotku_zdroj_a_obdobi(monkeypatch):
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "test_chart": {"source": "uzis-isin", "metric": "cases", "unit": "per_100k",
                       "grain": "month", "region": "CZ"},
    })
    monkeypatch.setattr(chart_meta, "_SOURCES", {
        "uzis-isin": {"publisher": "ÚZIS ČR", "title": "ISIN",
                      "modified_at_publisher": "2026-01-22T09:09:17Z",
                      "caveats": [{"id": "ews", "description": "zlom v hlášení"}]},
    })
    meta = chart_meta.meta_for("test_chart", {"labels": ["2018-01", "2025-12"], "datasets": []})
    assert meta["unit"] == "per_100k"
    assert meta["period"] == {"start": "2018-01", "end": "2025-12", "points": 2,
                              "start_date": "2018-01-01", "end_date": "2025-12-31"}
    assert meta["sources"][0]["modified_at_publisher"] == "2026-01-22T09:09:17Z"
    assert meta["caveats"][0]["id"] == "ews"


def test_meta_nese_citaci_zdroje(monkeypatch):
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "test_chart": {"source": "ecdc-erviss", "metric": "positivity", "unit": "percent",
                       "grain": "week", "region": "CZ"},
    })
    monkeypatch.setattr(chart_meta, "_SOURCES", {
        "ecdc-erviss": {"publisher": "ECDC / WHO Europe", "citation": "Dataset provided by ECDC"},
    })
    meta = chart_meta.meta_for("test_chart", {"labels": [], "datasets": []})
    assert meta["sources"][0]["citation"] == "Dataset provided by ECDC"


@pytest.mark.parametrize("labels,grain,expected", [
    (["2020-01-27", "2026-10-12"], "week", ("2020-01-27", "2026-10-18")),   # popisek = pondělí týdne
    (["2020-03-01", "2026-10-18"], "day", ("2020-03-01", "2026-10-18")),
    (["2018-01", "2024-02"], "month", ("2018-01-01", "2024-02-29")),        # přestupný únor
    (["2000", "2024"], "year", ("2000-01-01", "2024-12-31")),
    (["KT 40/26", "KT 41/26"], "week", ("2026-09-28", "2026-10-11")),       # ISO týdny
    (["KT 52/25", "KT 1/26"], "week", ("2025-12-22", "2026-01-04")),        # přes přelom roku
])
def test_obdobi_se_prevede_na_data(monkeypatch, labels, grain, expected):
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "g": {"source": "uzis-isin", "metric": "cases", "unit": "count", "grain": grain, "region": "CZ"},
    })
    period = chart_meta.meta_for("g", {"labels": labels, "datasets": []}, quiet=True)["period"]
    assert (period["start_date"], period["end_date"]) == expected
    assert (period["start"], period["end"]) == (labels[0], labels[-1]), "původní popisky zůstávají"


@pytest.mark.parametrize("labels,grain", [
    (["2012/2013", "2026/2027"], "season"),   # sezóna nemá v datech den, kterým začíná
    (["KT 54/26", "KT 55/26"], "week"),       # týden, který neexistuje
    (["2018-01", "leden"], "month"),          # jeden konec nečitelný → žádné datum, ne půlka
])
def test_obdobi_bez_jisteho_data_zustane_jen_popiskem(monkeypatch, labels, grain):
    """Datum odhadnuté z popisku by vypadalo stejně důvěryhodně jako skutečné."""
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "g": {"source": "uzis-isin", "metric": "cases", "unit": "count", "grain": grain, "region": "CZ"},
    })
    period = chart_meta.meta_for("g", {"labels": labels, "datasets": []}, quiet=True)["period"]
    assert period == {"start": labels[0], "end": labels[-1], "points": 2}


def test_meta_nese_nazev_a_ciselnik_rad(monkeypatch):
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "g": {"source": "uzis-isin", "metric": "cases", "unit": "count", "grain": "year",
              "region": "CZ", "series_code": "icd10"},
        "bez": {"source": "uzis-isin", "metric": "cases", "unit": "count", "grain": "year", "region": "CZ"},
    })
    monkeypatch.setattr(chart_meta, "_TITLES", {"g": {"cs": "Graf", "en": "Chart"}, "bez": {"cs": "Jen česky"}})
    meta = chart_meta.meta_for("g", {"datasets": [{"label": "Shigelóza", "code": "A03"}]}, quiet=True)
    assert meta["title"] == {"cs": "Graf", "en": "Chart"}
    assert meta["series_code_system"]["id"] == "ICD-10"
    souhrn = chart_meta.meta_for("g", {"datasets": [{"label": "Ostatní (souhrn)"}]}, quiet=True)
    assert "series_code_system" not in souhrn, "číselník jen tam, kde řady kód opravdu nesou"

    bez = chart_meta.meta_for("bez", quiet=True)
    assert "title" not in bez, "poloviční název se nevydává za úplný"
    assert "series_code_system" not in bez


def test_licence_vystupu_je_otevrena_jen_kdyz_to_dovoli_vsechny_zdroje(monkeypatch):
    monkeypatch.setattr(chart_meta, "_OPEN_SOURCES", {"uzis-isin", "csu-population"})
    assert chart_meta.licence_for(["uzis-isin", "csu-population"])["id"] == "CC-BY-4.0"

    mixed = chart_meta.licence_for(["uzis-isin", "szu-respiratory"])
    assert mixed["id"] == "source-terms"
    assert mixed["restricted_by"] == ["szu-respiratory"], "jen zdroj, jehož podmínky rozhodují"
    assert "url" not in mixed, "bez odkazu, který by sklízeč vzal za otevřenou licenci"

    # Zdroj, o kterém registr nic neví, a graf bez zdroje: nic se neslibuje.
    assert chart_meta.licence_for(["neznamy"])["id"] == "source-terms"
    assert chart_meta.licence_for([])["id"] == "source-terms"


def test_licence_grafu_podle_skutecneho_registru():
    """Nad skutečnými soubory: graf ze SZÚ nesmí vyjít pod CC BY, graf z ISIN ano."""
    assert chart_meta.meta_for("flu_weekly", quiet=True)["licence"]["id"] == "source-terms"
    assert chart_meta.meta_for("flu_mem", quiet=True)["licence"]["restricted_by"] == \
        ["who-fluid-flunet", "ecdc-erviss"]
    assert chart_meta.meta_for("isin_regional_incidence", quiet=True)["licence"]["id"] == "CC-BY-4.0"
    assert chart_meta.meta_for("covid_cases_weekly", quiet=True)["licence"]["id"] == "CC-BY-4.0"


def test_obdobi_se_necte_z_neCasove_osy(monkeypatch):
    """U věkových skupin by „od 0-4 do 80+“ vypadalo jako rozsah dat."""
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "vek": {"source": "uzis-isin", "metric": "cases", "unit": "count",
                "grain": "age_group", "region": "CZ"},
    })
    meta = chart_meta.meta_for("vek", {"labels": ["0-4", "80+"], "datasets": []})
    assert "period" not in meta


def test_skupinove_grafy_pokryva_hvezdicka(monkeypatch):
    monkeypatch.setattr(chart_meta, "_CHARTS", {
        "isin_group_*": {"source": "uzis-isin", "metric": "cases", "unit": "count",
                         "grain": "year", "region": "CZ"},
    })
    assert chart_meta.spec_for("isin_group_hepatitis") is not None
    assert chart_meta.spec_for("neco_jineho") is None


def test_graf_bez_zaznamu_projde_bez_metadat(monkeypatch, capsys):
    """Chybějící popisek nesmí shodit generování dat."""
    monkeypatch.setattr(chart_meta, "_CHARTS", {})
    obj = {"labels": ["a"], "datasets": []}
    assert chart_meta.attach("neznamy", obj) == obj
    assert "není v charts.yaml" in capsys.readouterr().out

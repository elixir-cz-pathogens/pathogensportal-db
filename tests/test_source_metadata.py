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
    assert meta["period"] == {"start": "2018-01", "end": "2025-12", "points": 2}
    assert meta["sources"][0]["modified_at_publisher"] == "2026-01-22T09:09:17Z"
    assert meta["caveats"][0]["id"] == "ews"


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

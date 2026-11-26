"""last_checked.py — datum „naposledy ověřeno u zdroje“ vedle data dat.

Datum u panelu se má hýbat jen s daty. Tenhle soubor nese druhé tvrzení: kdy se
pipeline zdroje naposledy zeptala a graf tomu odpovídal. Testy hlídají, že
  - ověření znamená skutečné ověření: zdroj, jehož scraper selhal, ani graf,
    jehož generátor nedoběhl, nové datum nedostane;
  - obsah se mění nejvýš jednou denně (v souboru jsou dny, ne časy) a soubor se
    beze změny nepřepisuje — jinak by každý hodinový běh znamenal commit;
  - datum se nikdy nevrací.
"""
import json
import re
from datetime import datetime, timezone

import pytest
import yaml

import last_checked as lc

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCES = {"signals": ["isin"], "incidence": ["isin", "population"], "genomics": ["ena"]}


def at(day: int, hour: int = 6, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Pracovní adresář se třemi grafy; stav jde do tmp (viz conftest)."""
    out = tmp_path / "out"
    out.mkdir()
    for name in SOURCES:
        (out / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lc, "OUTPUT_DIR", out)
    monkeypatch.setattr(lc, "_chart_sources", SOURCES.get)
    return out


def run(day, hour=6, checked=("isin", "population", "ena"), charts=tuple(SOURCES)):
    """Jeden běh pipeline: scrapery → generátory → last_checked.py."""
    lc.record_sources(checked, at(day, hour))
    for name in charts:
        lc.mark_chart(name, at(day, hour, 5))
    assert lc.main() == 0


def published(ws) -> dict:
    return json.loads((ws / lc.OUT_NAME).read_text(encoding="utf-8"))


def checked(ws, name) -> str:
    return published(ws)["charts"][name]["checked"]


def test_overeny_graf_dostane_dnesni_datum(ws):
    run(26)
    doc = published(ws)
    assert doc["charts"] == {name: {"checked": "2026-11-26"} for name in sorted(SOURCES)}
    assert doc["sources"] == {sid: {"checked": "2026-11-26"} for sid in ("ena", "isin", "population")}
    assert doc["timezone"] == "UTC"


def test_zdroj_se_selhanym_scraperem_datum_neposune(ws):
    run(25)
    run(26, checked=("isin", "population"))          # archiv ENA neodpověděl
    assert checked(ws, "genomics") == "2026-11-25"
    assert checked(ws, "signals") == "2026-11-26"
    assert published(ws)["sources"]["ena"] == {"checked": "2026-11-25"}
    run(27)                                           # další den se ENA vrátila
    assert checked(ws, "genomics") == "2026-11-27"


def test_graf_z_vice_zdroju_ceka_na_vsechny(ws):
    run(25)
    run(26, checked=("isin",))                        # populace selhala
    assert checked(ws, "signals") == "2026-11-26"     # stojí jen na ISIN
    assert checked(ws, "incidence") == "2026-11-25"   # žádný zdroj není čerstvější, než tvrdíme


def test_graf_jehoz_generator_nedobehl_datum_neposune(ws):
    run(25)
    # Zdroje ověřené, ale generátor za `||` spadl: graf zůstal z 25. 11.
    run(26, charts=("signals", "incidence"))
    assert checked(ws, "genomics") == "2026-11-25"
    assert checked(ws, "signals") == "2026-11-26"


def test_zdroj_ktery_nikdy_nebyl_overen_graf_neoveri(ws):
    run(26, checked=("isin",))
    assert set(published(ws)["charts"]) == {"signals"}


def test_druhy_beh_tehoz_dne_soubor_neprepise(ws, capsys):
    run(26, hour=6)
    path = ws / lc.OUT_NAME
    first = path.read_bytes()
    path.touch()
    stamp = path.stat().st_mtime_ns
    for hour in (7, 8, 23):
        run(26, hour=hour)
    assert path.read_bytes() == first
    assert path.stat().st_mtime_ns == stamp           # nepřepsáno → žádný hodinový commit
    assert "beze změny" in capsys.readouterr().out
    run(27, hour=0)                                   # nový den → jedna změna
    assert path.read_bytes() != first
    assert checked(ws, "signals") == "2026-11-27"


def test_v_souboru_jsou_jen_dny_zadny_cas(ws):
    """Čas by měnil obsah při každém běhu; a pole jako generated_at / fetched_at
    automat na serveru při rozhodování o commitu přehlíží."""
    run(26)
    text = (ws / lc.OUT_NAME).read_text(encoding="utf-8")
    assert "generated_at" not in text and "fetched_at" not in text
    assert not re.search(r"\d{2}:\d{2}", text)
    doc = json.loads(text)
    for section in ("charts", "sources"):
        for rec in doc[section].values():
            assert set(rec) == {"checked"} and DAY.match(rec["checked"])


def test_beh_pres_pulnoc_netvrdi_novejsi_den_nez_dotaz(ws):
    lc.record_sources(("isin",), at(26, 23, 58))      # začátek běhu
    lc.mark_chart("signals", at(27, 0, 3))            # generátor doběhl po půlnoci
    lc.main()
    assert checked(ws, "signals") == "2026-11-26"


def test_datum_se_nevraci_ani_po_ztrate_stavu(ws):
    run(26)
    lc.STATE_FILE.unlink()                            # nový svazek /data
    assert lc.main() == 0
    assert checked(ws, "signals") == "2026-11-26"     # drží se, co už portál ukazuje
    lc.record_sources(("isin",), at(20))              # hodiny zpět / obnovená záloha
    lc.mark_chart("signals", at(20, 6, 5))
    lc.main()
    assert checked(ws, "signals") == "2026-11-26"


def test_bez_stavu_se_soubor_nezaklada(ws):
    assert lc.main() == 0
    assert not (ws / lc.OUT_NAME).exists()


def test_zaznam_o_zmizelem_grafu_se_nenese_dal(ws):
    run(25)
    (ws / "genomics.json").unlink()
    run(26, charts=("signals", "incidence"))
    assert "genomics" not in published(ws)["charts"]


def test_poskozeny_stav_ani_vystup_beh_neshodi(ws):
    lc.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lc.STATE_FILE.write_text("{ rozbité", encoding="utf-8")
    (ws / lc.OUT_NAME).write_text('{"charts": ["není", "mapa"], "sources": 3}', encoding="utf-8")
    run(26)
    assert checked(ws, "signals") == "2026-11-26"

    lc.STATE_FILE.write_text('{"sources": {"isin": 20261127, "ena": null}, "charts": []}', encoding="utf-8")
    assert lc.load_state() == {"sources": {}, "charts": {}}
    assert lc.main() == 0
    assert checked(ws, "signals") == "2026-11-26"


def test_mark_chart_neshodi_generator_kdyz_stav_nejde_zapsat(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "soubor"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setattr(lc, "STATE_FILE", blocker / "meta" / "checks.json")   # rodič je soubor
    lc.mark_chart("signals")
    assert "datum ověření se neposune" in capsys.readouterr().out


# ── Napojení na skutečný registr a na generátory ─────────────────────────────

def test_zdroje_grafu_se_ctou_z_charts_yaml():
    assert lc._chart_sources("anomaly_signals") == ["uzis-isin"]
    # signály z týdenního souhrnu mají vlastní zdroj: jejich datum ověření se
    # řídí jím (posouvá se zhruba týdně), ne otevřeným CSV ISIN
    assert lc._chart_sources("anomaly_signals_weekly") == ["uzis-isin-weekly"]
    assert lc._chart_sources("isin_group_other") == ["uzis-isin"]          # pod hvězdičkou
    assert lc._chart_sources("flu_mem") == ["who-fluid-flunet", "ecdc-erviss"]
    assert lc._chart_sources("last_checked") is None                       # sám sebe neověřuje


def test_neprepsany_soubor_se_presto_hlasi_jako_dokonceny(tmp_path, monkeypatch):
    """compute_isin.py soubor beze změny dat nechá být, aby se nehýbalo datum dat.
    Ověřený ale je — jinak by se „naposledy ověřeno“ u ročních dat nepohnulo nikdy."""
    import compute_isin as ci
    monkeypatch.setattr(ci, "OUTPUT_DIR", tmp_path / "out")
    obj = {"posledni_obdobi": "2025-12", "blocks": {}}
    ci.save("isin_overview", obj)
    path = tmp_path / "out" / "isin_overview.json"
    before = path.read_bytes()
    first = lc.load_state()["charts"]["isin_overview"]

    monkeypatch.setattr(lc, "_stamp", lambda now=None: "2099-01-01T00:00:00Z")
    ci.save("isin_overview", obj)
    assert path.read_bytes() == before                                     # datum dat se nehnulo
    assert lc.load_state()["charts"]["isin_overview"] == "2099-01-01T00:00:00Z" != first


def test_generate_json_save_hlasi_graf(tmp_path, monkeypatch):
    import generate_json as g
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path / "out")
    g.save("covid_summary", {"posledni_datum": "2026-11-25"})
    assert "covid_summary" in lc.load_state()["charts"]


def test_kazdy_generator_hlasi_dokonceni_grafu():
    """Pojistka proti novému generátoru, který by zapisoval JSON a ověření nehlásil —
    jeho grafy by zůstaly bez data a nikdo by si toho nevšiml."""
    scripts = lc.ROOT / "scripts"
    writers = [p for p in sorted(scripts.glob("*.py"))
               if "chart_meta.attach(" in p.read_text(encoding="utf-8") and p.name != "chart_meta.py"]
    assert len(writers) >= 7, "nenašel jsem generátory — změnil se tvar zápisu?"
    silent = [p.name for p in writers if "last_checked.mark_chart(" not in p.read_text(encoding="utf-8")]
    assert not silent, f"generátor zapisuje graf a nehlásí ho last_checked.mark_chart(): {silent}"


def test_posledni_krok_kontejneru_je_last_checked():
    """Musí běžet až po všech generátorech a jeho selhání nesmí zastavit řetěz."""
    cmd = [line for line in (lc.ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
           if line.startswith("CMD")][0]
    steps = re.findall(r"scripts/(\w+)\.py", cmd)
    assert steps[-1] == "last_checked"
    assert "(python scripts/last_checked.py ||" in cmd


def test_zapis_je_platny_json_s_jednim_grafem_na_radek(ws):
    run(26)
    text = (ws / lc.OUT_NAME).read_text(encoding="utf-8")
    assert json.loads(text) == lc.build(lc.load_state(), {}, SOURCES.get)
    assert '  "signals": {"checked": "2026-11-26"}' in text.splitlines() \
        or '  "signals": {"checked": "2026-11-26"},' in text.splitlines()
    assert lc._dump({"timezone": "UTC", "charts": {}, "sources": {}}).count("{}") == 2
    assert json.loads(lc._dump({"timezone": "UTC", "charts": {}, "sources": {}}))

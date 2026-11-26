"""run_all.py — které zdroje smějí zastavit obnovu dat a které ne.

Návratový kód 1 zastaví v kontejneru celý řetěz (run_all && generate_json && …),
takže portál nedostane žádná nová data. Genomické zdroje jsou tři cizí archivy,
na kterých nezávisí žádný jiný graf: jejich výpadek to způsobit nesmí.
"""
import pytest

# run_all importuje všechny scrapery včetně těch, které čtou PDF.
pytest.importorskip("pdfplumber")

import run_all  # noqa: E402

SCRAPERS = ("mzcr_covid", "ecdc_covid", "ecdc_erviss", "ecdc_erviss_snapshots", "ecdc_respicast",
            "ena_czech", "ncbi_pathogen", "nextstrain_czech", "szu_influenza", "szu_weekly",
            "uzis_isin", "uzis_isin_weekly", "uzis_registries", "csu_population", "who_flu")


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Všechny scrapery nahradí prázdným úspěchem — test nesmí sáhnout na síť ani na data/."""
    monkeypatch.setattr(run_all, "DATA_ROOT", tmp_path)
    for name in SCRAPERS:
        monkeypatch.setattr(getattr(run_all, name), "download", lambda out: [])
    monkeypatch.setattr(run_all.szu_influenza, "download_current", lambda out: [])

    import source_metadata

    def no_metadata():
        raise RuntimeError("v testu se metadata nesbírají")
    monkeypatch.setattr(source_metadata, "collect", no_metadata)
    return monkeypatch


def _fail(out):
    raise ConnectionError("archiv neodpovídá")


def test_all_sources_ok_returns_zero(offline):
    assert run_all.run() == 0


@pytest.mark.parametrize("scraper", ["ena_czech", "nextstrain_czech", "ncbi_pathogen", "uzis_isin_weekly"])
def test_side_source_outage_does_not_stop_the_pipeline(offline, scraper):
    offline.setattr(getattr(run_all, scraper), "download", _fail)
    assert run_all.run() == 0


def test_required_source_outage_still_fails(offline):
    offline.setattr(run_all.ecdc_erviss, "download", _fail)
    assert run_all.run() == 1


# ── Co se v běhu opravdu ověřilo (last_checked.py) ───────────────────────────
# Datum „naposledy ověřeno“ u panelu smí posunout jen běh, který se zdroje
# zeptal. Scraper, který selhal nebo vrátil soubory z disku, to není.

def _checked(tmp_path) -> set:
    import json
    return set(json.loads((tmp_path / "meta" / "checks.json").read_text(encoding="utf-8"))["sources"])


def _registry():
    import yaml
    root = run_all.Path(run_all.__file__).resolve().parents[1]
    sources = {s["id"] for s in yaml.safe_load((root / "sources.yaml").read_text(encoding="utf-8"))}
    used = set()
    for spec in yaml.safe_load((root / "charts.yaml").read_text(encoding="utf-8")).values():
        used |= set(spec["source"] if isinstance(spec["source"], list) else [spec["source"]])
    return sources, used


def test_successful_run_records_every_source_a_chart_stands_on(offline, tmp_path):
    assert run_all.run() == 0
    sources, used_by_charts = _registry()
    checked = _checked(tmp_path)
    assert checked <= sources, f"úloha ověřuje zdroj, který není v sources.yaml: {checked - sources}"
    # Graf se zdrojem, který žádná úloha neověřuje, by datum nedostal nikdy.
    assert used_by_charts <= checked, f"zdroj grafu neověřuje žádná úloha: {used_by_charts - checked}"


def test_failed_side_source_is_not_recorded_as_checked(offline, tmp_path):
    offline.setattr(run_all.ena_czech, "download", _fail)
    assert run_all.run() == 0
    checked = _checked(tmp_path)
    assert "ena" not in checked
    assert {"nextstrain", "uzis-isin", "mzcr-covid"} <= checked


def test_source_served_from_cache_is_not_recorded_as_checked(offline, tmp_path):
    def from_cache(out):
        return []
    from_cache.source_checked = False
    offline.setattr(run_all.uzis_isin_weekly, "download", from_cache)
    assert run_all.run() == 0
    checked = _checked(tmp_path)
    assert "uzis-isin-weekly" not in checked and "uzis-isin" in checked


def test_source_with_two_jobs_needs_both(offline, tmp_path):
    offline.setattr(run_all.szu_weekly, "download", _fail)      # download_current uspěl
    assert run_all.run() == 1
    checked = _checked(tmp_path)
    assert "szu-respiratory" not in checked
    assert "who-fluid-flunet" in checked                         # záznam je pravdivý i při exit 1


def test_earlier_check_survives_a_failed_run(offline, tmp_path):
    import json
    assert run_all.run() == 0
    state = tmp_path / "meta" / "checks.json"
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["sources"] = {sid: "2026-01-01T00:00:00Z" for sid in doc["sources"]}     # „včerejší“ běh
    state.write_text(json.dumps(doc), encoding="utf-8")

    offline.setattr(run_all.ena_czech, "download", _fail)
    assert run_all.run() == 0
    sources = json.loads(state.read_text(encoding="utf-8"))["sources"]
    assert sources["ena"] == "2026-01-01T00:00:00Z"              # výpadek čas ověření nepřepíše
    assert sources["uzis-isin"] > "2026-01-01T00:00:00Z"         # ověřený zdroj se posunul

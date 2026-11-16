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
            "uzis_isin", "uzis_registries", "csu_population", "who_flu")


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


@pytest.mark.parametrize("scraper", ["ena_czech", "nextstrain_czech", "ncbi_pathogen"])
def test_genomic_source_outage_does_not_stop_the_pipeline(offline, scraper):
    offline.setattr(getattr(run_all, scraper), "download", _fail)
    assert run_all.run() == 0


def test_required_source_outage_still_fails(offline):
    offline.setattr(run_all.ecdc_erviss, "download", _fail)
    assert run_all.run() == 1

"""ECDC COVID-19 — zmrazená archivní sada se po prvním stažení už nestahuje.

Hlídá, aby nedostupný server ECDC znovu neshodil celou pipeline (30. 9. 2026).
"""
from scrapers import ecdc_covid


def test_existing_archive_is_not_downloaded_again(tmp_path, monkeypatch):
    (tmp_path / "ecdc_covid_cz.csv").write_text("datum,nove_pripady,nove_umrti,populace\n", encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("ECDC se nemá volat, archiv už existuje")

    monkeypatch.setattr(ecdc_covid.requests, "get", no_network)
    assert ecdc_covid.download(tmp_path) == [str(tmp_path / "ecdc_covid_cz.csv")]


def test_missing_archive_is_downloaded(tmp_path, monkeypatch):
    csv = ("dateRep,day,month,year,cases,deaths,countriesAndTerritories,geoId,popData2020\n"
           "02/01/2022,2,1,2022,10,1,Czechia,CZ,10700000\n"
           "01/01/2022,1,1,2022,5,0,Czechia,CZ,10700000\n"
           "01/01/2022,1,1,2022,7,0,Austria,AT,8900000\n")

    class Response:
        content = csv.encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ecdc_covid.requests, "get", lambda *a, **k: Response())
    ecdc_covid.download(tmp_path)
    lines = (tmp_path / "ecdc_covid_cz.csv").read_text(encoding="utf-8").splitlines()
    assert lines == ["datum,nove_pripady,nove_umrti,populace", "2022-01-01,5,0,10700000", "2022-01-02,10,1,10700000"]

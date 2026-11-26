"""ČSÚ — obyvatelstvo podle jednotek věku (csu_population.parse_age, download).

Věk je doplněk pro přepočet věkových skupin na 100 tis. obyvatel. Jeho výpadek
nesmí zastavit obnovu ostatních dat: csu_population je povinný zdroj a chyba
z něj by v run_all.py zastavila celý řetěz.
"""
import pytest

from scrapers import csu_population as c


def _csv(rows):
    head = '"Ukazatel","Věk (roky)","Území","Roky","Hodnota","MJ_TEXT"\n'
    return (head + "".join(f'"{u}","{a}","Česko","{y}","{v}","osoba"\n' for u, a, y, v in rows)).encode("utf-8")


def _full(year=2025, drop=None):
    rows = []
    for sex, n in (("Počet obyvatel - muži", 50.0), ("Počet obyvatel - ženy", 60.0)):
        if sex == drop:
            continue
        rows += [(sex, str(a) if a < 100 else "100 a více", year, n) for a in range(101)]
    return _csv(rows)


def test_secte_obe_pohlavi_a_otevreny_konec_ulozi_jako_100():
    out = c.parse_age(_full())
    assert len(out) == 101 and set(out["pocet"]) == {110}
    assert out["vek"].max() == c.AGE_OPEN_END and out["rok"].unique().tolist() == [2025]


def test_rok_bez_jednoho_pohlavi_se_ohlasi():
    with pytest.raises(ValueError, match="obě pohlaví"):
        c.parse_age(_full(drop="Počet obyvatel - ženy"))


def test_vypadek_veku_nezastavi_stazeni_populace(tmp_path, monkeypatch):
    regions = "".join(f'2025,"{name}","Celkem",1000\n' for name in c.REGION_TO_NUTS3)

    class Response:
        content = ('"Roky","ČR, kraje","Pohlaví","Hodnota"\n' + regions).encode("utf-8")

        def raise_for_status(self):
            pass

    def get(url, timeout):
        if url == c.AGE_URL:
            raise ConnectionError("sada věku neodpovídá")
        return Response()
    monkeypatch.setattr(c.requests, "get", get)
    files = c.download(tmp_path)
    assert [f.rsplit("/", 1)[-1] for f in files] == ["population.csv"]
    # běh nespadl, ale půlka zdroje je z minula — za ověření se to nepočítá
    assert c.download.source_checked is False

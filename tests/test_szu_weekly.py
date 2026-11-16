"""
Matice virů ze SZÚ PDF — na skutečných PDF obou rozvržení, bez sítě.

KT 39/2026 je poslední PDF se dvěma tabulkami (minulá + běžící sezóna).
KT 40/2026 je první se třemi: prostřední rok je v hlavičce jako „###“, sloupce
„Kumulativně“ jsou dva, chybí popisek „Detekce viru“ a sloupce jsou tak úzké,
že se sousední čísla slévají. Na tom pipeline 6. 10. 2026 přestala běžet.
KT 42/2026 má nad maticí nový souhrn „Chřipky: sum A, B, A+B“ s vlastním řádkem
týdnů, značky tabulek jsou „2024/2025“ místo roku a „Kumulativně“ stojí těsně za
týdnem 39. Na tom pipeline přestala běžet podruhé (26. 10. 2026).
Čísla tu testovat jde, protože publikované PDF se už nemění.

Krajská PDF (jeden týden na soubor): 16. 11. 2026 SZÚ vystavil pod jménem
„46. týden“ opravenou tabulku 45. týdne (přibyl Pardubický kraj). Kraj se tak
v jednom týdnu objevil dvakrát a generování grafů na tom spadlo — potřetí.
"""

from pathlib import Path

import pandas as pd
import pytest

from scrapers.szu_influenza import season_totals
from scrapers.szu_weekly import (_load_kraje_pdf, merge_kraje, parse_kraje,
                                 parse_viry_matrix)

FIX = Path(__file__).parent / "fixtures" / "szu"


def _matrix(week: int) -> pd.DataFrame:
    pdf = FIX / f"laboratorni_vysetreni_podle_typu_viru_{week}_tyden_2026.pdf"
    return pd.DataFrame(parse_viry_matrix(pdf.read_bytes()))


def test_two_table_layout():
    m = _matrix(39)
    assert sorted(m["sezona"].unique()) == ["2024_2025", "2025_2026"]
    assert len(m) == 1189


def test_three_table_layout():
    m = _matrix(40)
    assert sorted(m["sezona"].unique()) == ["2024_2025", "2025_2026", "2026_2027"]
    new = m[m["sezona"] == "2026_2027"]
    assert set(new["tyden"]) == {40} and set(new["rok"]) == {2026}
    assert new.set_index("virus")["pocet"].to_dict() == {
        "Influenza A": 1, "Influenza B": 1, "Adenovirus": 1, "Parainfluenza": 5,
        "Metapneumovirus": 1, "Rhinovirus": 45, "Enterovirus": 1, "SARS-CoV-2": 3,
        "Smíšená infekce": 1,
    }


def test_summary_block_layout():
    m = _matrix(42)
    assert sorted(m["sezona"].unique()) == ["2024_2025", "2025_2026", "2026_2027"]
    new = m[m["sezona"] == "2026_2027"]
    # „Kumulativně“ za týdnem 39 se nesmí přičíst k žádnému týdnu
    assert set(zip(new["rok"], new["tyden"])) == {(2026, 40), (2026, 41), (2026, 42)}
    flu_a = new[new["virus"] == "Influenza A"].set_index("tyden")["pocet"].to_dict()
    assert flu_a == {40: 1, 41: 7, 42: 1}


@pytest.mark.parametrize("week", [39, 40, 42])
def test_no_glued_numbers(week):
    # slitá čísla ze sousedních sloupců dávají nesmyslně velké týdenní hodnoty
    assert _matrix(week)["pocet"].max() < 1000


def test_layouts_agree_on_closed_weeks():
    # Sezóna 2025/26 je v obou PDF celá; nové rozvržení ji musí přečíst stejně.
    # Jediný povolený rozdíl je zpětná korekce SZÚ mezi týdny.
    key = ["sezona", "rok", "tyden", "virus"]
    a = _matrix(39).query("sezona == '2025_2026'").set_index(key)["pocet"]
    b = _matrix(40).query("sezona == '2025_2026'").set_index(key)["pocet"]
    both = a.index.intersection(b.index)
    assert len(both) > 0.95 * max(len(a), len(b))
    assert (a[both] - b[both]).abs().sum() <= 0.01 * a.sum()


def test_season_totals_shape():
    totals = season_totals(_matrix(40).to_dict("records"))
    cur = totals["2025_2026"]
    assert list(cur.columns) == ["sezona", "rok", "tyden_kt", "kategorie", "virus", "pocet"]
    assert set(cur["rok"]) == {2026} and set(cur["tyden_kt"]) == {0}
    assert set(cur["kategorie"]) == {"Detekce viru"}
    # souhrn sedí na sloupec „Kumulativně“ v PDF (validace v parseru)
    assert cur.set_index("virus").loc["Influenza A", "pocet"] == 3361
    assert cur.set_index("virus").loc["RSV", "pocet"] == 1571


# ── krajská PDF ──────────────────────────────────────────────────────────────

KRAJE_URL = "https://szu.gov.cz/wp-content/uploads/2026/11/laboratorni_vysetreni_podle_kraju_{}_tyden_2026.pdf"


def _kraje_bytes(file_week: int) -> bytes:
    return (FIX / f"laboratorni_vysetreni_podle_kraju_{file_week}_tyden_2026.pdf").read_bytes()


def _parsed(file_week: int):
    year, week, rows = parse_kraje(_kraje_bytes(file_week))
    return (f"kraju_{file_week}.pdf", year, week, rows)


def test_week_is_read_from_the_table_not_the_file_name():
    # soubor „46. týden“ nese 45. týden; liší se jen Pardubickým krajem
    for file_week in (45, 46):
        _, year, week, rows = _parsed(file_week)
        assert (year, week) == (2026, 45)
        assert len(rows) == 11
    pardubice = [{r["kraj"]: (r["pozitivni"], r["vysetreno"]) for r in _parsed(w)[3]}["Pardubický kraj"]
                 for w in (45, 46)]
    assert pardubice == [(0, 0), (3, 54)]


def test_later_pdf_replaces_the_same_week():
    rows = merge_kraje([_parsed(45), _parsed(46)])
    df = pd.DataFrame(rows)
    assert not df.duplicated(["rok", "tyden", "kraj"]).any()
    assert len(df) == 11 and set(df["tyden"]) == {45}
    assert df.set_index("kraj").loc["Pardubický kraj", "vysetreno"] == 54
    # součet sedí na řádek „Celkový počet vyšetření“ opraveného PDF
    assert (df["pozitivni"].sum(), df["vysetreno"].sum()) == (127, 913)


def test_merge_keeps_distinct_weeks():
    a = ("a.pdf", 2026, 44, [{"kraj": "Vysočina", "pozitivni": 1, "vysetreno": 9}])
    b = ("b.pdf", 2026, 45, [{"kraj": "Vysočina", "pozitivni": 2, "vysetreno": 8}])
    assert merge_kraje([a, b]) == [
        {"rok": 2026, "tyden": 44, "kraj": "Vysočina", "pozitivni": 1, "vysetreno": 9},
        {"rok": 2026, "tyden": 45, "kraj": "Vysočina", "pozitivni": 2, "vysetreno": 8},
    ]


class _Fetch:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, 0

    def __call__(self, url):
        self.calls += 1
        if self.error:
            raise self.error
        return self.content


def test_cached_pdf_is_not_downloaded_again(tmp_path):
    (tmp_path / "laboratorni_vysetreni_podle_kraju_45_tyden_2026.pdf").write_bytes(_kraje_bytes(45))
    fetch = _Fetch(error=AssertionError("síť se nemá volat"))
    year, week, _ = _load_kraje_pdf(KRAJE_URL.format(45), tmp_path, fetch)
    assert (year, week) == (2026, 45) and fetch.calls == 0


def test_pdf_whose_name_and_table_disagree_is_downloaded_every_run(tmp_path):
    # v cache leží „46“ s tabulkou 45. týdne; SZÚ ho mezitím pod stejným jménem
    # vyměnil (tady za jiný obsah — skutečný 46. týden ještě neexistuje)
    cached = tmp_path / "laboratorni_vysetreni_podle_kraju_46_tyden_2026.pdf"
    cached.write_bytes(_kraje_bytes(46))
    fetch = _Fetch(content=_kraje_bytes(45))
    _, _, rows = _load_kraje_pdf(KRAJE_URL.format(46), tmp_path, fetch)
    assert fetch.calls == 1
    assert cached.read_bytes() == _kraje_bytes(45)
    assert {r["kraj"]: r["vysetreno"] for r in rows}["Pardubický kraj"] == 0


def test_failed_redownload_keeps_the_cached_pdf(tmp_path):
    cached = tmp_path / "laboratorni_vysetreni_podle_kraju_46_tyden_2026.pdf"
    cached.write_bytes(_kraje_bytes(46))
    fetch = _Fetch(error=OSError("spojení selhalo"))
    year, week, rows = _load_kraje_pdf(KRAJE_URL.format(46), tmp_path, fetch)
    assert fetch.calls == 1 and (year, week) == (2026, 45) and len(rows) == 11
    assert cached.read_bytes() == _kraje_bytes(46)


def test_new_pdf_is_downloaded_once(tmp_path):
    fetch = _Fetch(content=_kraje_bytes(46))
    _load_kraje_pdf(KRAJE_URL.format(46), tmp_path, fetch)
    assert fetch.calls == 1
    assert (tmp_path / "laboratorni_vysetreni_podle_kraju_46_tyden_2026.pdf").exists()

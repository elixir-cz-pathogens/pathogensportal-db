"""
Matice virů ze SZÚ PDF — na skutečných PDF obou rozvržení, bez sítě.

KT 39/2026 je poslední PDF se dvěma tabulkami (minulá + běžící sezóna).
KT 40/2026 je první se třemi: prostřední rok je v hlavičce jako „###“, sloupce
„Kumulativně“ jsou dva, chybí popisek „Detekce viru“ a sloupce jsou tak úzké,
že se sousední čísla slévají. Na tom pipeline 6. 10. 2026 přestala běžet.
Čísla tu testovat jde, protože publikované PDF se už nemění.
"""

from pathlib import Path

import pandas as pd
import pytest

from scrapers.szu_influenza import season_totals
from scrapers.szu_weekly import parse_viry_matrix

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


@pytest.mark.parametrize("week", [39, 40])
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

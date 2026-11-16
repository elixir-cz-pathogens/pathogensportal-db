"""Krajské grafy ze SZÚ — jeden řádek na kraj a týden, i když ho CSV nese dvakrát.

16. 11. 2026 SZÚ vystavil opravený 45. týden pod jménem 46. týdne; kraj byl
v szu_weekly_kraje.csv v jednom týdnu dvakrát, `flu_regional_weekly` na tom
spadl a s ním celý řetěz kontejneru — nevznikl žádný graf.
"""
import json

import pandas as pd
import pytest

import generate_json as g

ROWS = [
    (2026, 44, "Pardubický kraj", 4, 40),
    (2026, 44, "Vysočina", 1, 20),
    (2026, 45, "Pardubický kraj", 0, 0),      # první zveřejnění: laboratoř nehlásila
    (2026, 45, "Pardubický kraj", 3, 54),     # oprava
    (2026, 45, "Vysočina", 3, 50),
    (2026, 45, "Vysočina", 3, 50),
]


@pytest.fixture
def charts(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "DATA_DIR", tmp_path)
    monkeypatch.setattr(g, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "szu").mkdir()
    pd.DataFrame(ROWS, columns=["rok", "tyden", "kraj", "pozitivni", "vysetreno"]).to_csv(
        tmp_path / "szu" / "szu_weekly_kraje.csv", index=False)

    def read(name):
        chart = json.loads((tmp_path / "out" / f"{name}.json").read_text(encoding="utf-8"))
        return chart["labels"], {d["label"]: d["data"] for d in chart["datasets"]}

    return read


def test_weekly_chart_survives_a_doubled_week(charts):
    g.flu_regional_weekly()
    labels, data = charts("flu_regional_weekly")
    assert labels == ["KT 44/26", "KT 45/26"]
    assert data == {"Pardubický": [4, 3], "Vysočina": [1, 3]}


def test_season_totals_do_not_count_a_week_twice(charts):
    g.flu_regional_overview()
    labels, data = charts("flu_regional_overview")
    totals = dict(zip(labels, zip(data["Pozitivní záchyty"], data["Vyšetřeno vzorků"])))
    assert totals == {"Pardubický": (7, 94), "Vysočina": (4, 70)}

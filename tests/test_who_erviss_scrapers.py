"""
Scrapery WHO FluNet/FluID a ECDC ERVISS — bez sítě, s podstrčenou HTTP odpovědí.

Hlídají to, co se u cizího zdroje rozbije potichu: změnu sloupců, prázdný filtr
na ČR a zdroj, který přestal přibývat. Čísla samotná netestujeme (mění se).
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from scrapers import ecdc_erviss, who_flu


class _Resp:
    def __init__(self, text: str):
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        pass


def _serve(monkeypatch, module, by_url_part: dict[str, str]):
    def fake_get(url, **kwargs):
        for part, text in by_url_part.items():
            if part in url:
                return _Resp(text)
        raise AssertionError(f"nečekaná URL {url}")
    monkeypatch.setattr(module.requests, "get", fake_get)


def _monday(days_ago: int) -> date:
    d = date.today() - timedelta(days=days_ago)
    return d - timedelta(days=d.weekday())


def _flunet_csv(week_start: date, drop: str | None = None) -> str:
    y, w, _ = week_start.isocalendar()
    cols = ["COUNTRY_CODE"] + list(who_flu.FLUNET_COLS)
    row = {"COUNTRY_CODE": "CZE", "ISO_YEAR": y, "ISO_WEEK": w,
           "ISO_WEEKSTARTDATE": f"{week_start}T00:00:00", "ORIGIN_SOURCE": "NONSENTINEL",
           "SPEC_PROCESSED_NB": 2822, "INF_A": 472, "INF_B": 286, "INF_ALL": 758,
           "AH1N12009": 39, "AH3": 8, "ANOTSUBTYPED": 425, "RSV": ""}
    if drop:
        cols.remove(drop)
    return ",".join(cols) + "\n" + ",".join(str(row[c]) for c in cols) + "\n"


def _fluid_csv(week_start: date) -> str:
    y, w, _ = week_start.isocalendar()
    cols = ["COUNTRY_CODE"] + list(who_flu.FLUID_COLS)
    row = {"COUNTRY_CODE": "CZE", "ISO_YEAR": y, "ISO_WEEK": w,
           "ISO_WEEKSTARTDATE": f"{week_start}T00:00:00", "AGEGROUP_CODE": "All",
           "ILI_CASE": 448, "ILI_POP_COV": 8837792, "ARI_CASE": 35830, "ARI_POP_COV": 8837792}
    return ",".join(cols) + "\n" + ",".join(str(row[c]) for c in cols) + "\n"


def test_who_flu_writes_both_files(monkeypatch, tmp_path):
    _serve(monkeypatch, who_flu, {"VIW_FNT": _flunet_csv(_monday(7)),
                                  "VIW_FID_EPI": _fluid_csv(_monday(7))})
    files = who_flu.download(tmp_path)

    flunet = pd.read_csv(files[0])
    assert list(flunet.columns) == list(who_flu.FLUNET_COLS.values())
    assert flunet.loc[0, "vysetreno"] == 2822 and flunet.loc[0, "inf_celkem"] == 758
    # prázdná buňka = nehlášeno; nula by z RSV udělala "žádný záchyt"
    assert pd.isna(flunet.loc[0, "rsv"])

    fluid = pd.read_csv(files[1])
    assert fluid.loc[0, "ili_pripady"] == 448 and fluid.loc[0, "vek"] == "All"


def test_who_flu_fails_on_missing_column(monkeypatch, tmp_path):
    _serve(monkeypatch, who_flu, {"VIW_FNT": _flunet_csv(_monday(7), drop="SPEC_PROCESSED_NB")})
    with pytest.raises(ValueError, match="SPEC_PROCESSED_NB"):
        who_flu.download(tmp_path)


def test_who_flu_fails_on_stale_source(monkeypatch, tmp_path):
    stale = _monday(who_flu.MAX_AGE_DAYS["VIW_FNT"] + 14)
    _serve(monkeypatch, who_flu, {"VIW_FNT": _flunet_csv(stale)})
    with pytest.raises(ValueError, match="přestal přibývat"):
        who_flu.download(tmp_path)


def _yearweek(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _erviss(monkeypatch, rates: str, lab: str | None = None):
    lab = lab or ("survtype,countryname,yearweek,pathogen,pathogentype,pathogensubtype,indicator,age,value\n"
                  f"non-sentinel,Czechia,{_yearweek(_monday(7))},Influenza,Influenza,total,tests,total,304\n")
    _serve(monkeypatch, ecdc_erviss, {"ILIARIRates": rates, "nonSentinel": lab})


def test_erviss_filters_czechia_and_renames_indicator(monkeypatch, tmp_path):
    yw = _yearweek(_monday(7))
    _erviss(monkeypatch,
            "survtype,countryname,yearweek,indicator,age,value\n"
            f"primary care syndromic,Austria,{yw},ILIconsultationrate,total,857.6\n"
            f"primary care syndromic,Czechia,{yw},ILIconsultationrate,total,5.1\n")
    files = ecdc_erviss.download(tmp_path)

    rates = pd.read_csv(files[0])
    assert len(rates) == 1
    assert rates.loc[0, "ukazatel"] == "ILI" and rates.loc[0, "mira_na_100k"] == 5.1


def test_erviss_fails_when_czechia_missing(monkeypatch, tmp_path):
    _erviss(monkeypatch,
            "survtype,countryname,yearweek,indicator,age,value\n"
            f"primary care syndromic,Austria,{_yearweek(_monday(7))},ILIconsultationrate,total,857.6\n")
    with pytest.raises(ValueError, match="Czechia"):
        ecdc_erviss.download(tmp_path)


def test_erviss_fails_on_unknown_indicator(monkeypatch, tmp_path):
    _erviss(monkeypatch,
            "survtype,countryname,yearweek,indicator,age,value\n"
            f"primary care syndromic,Czechia,{_yearweek(_monday(7))},SARIrate,total,1.0\n")
    with pytest.raises(ValueError, match="SARIrate"):
        ecdc_erviss.download(tmp_path)

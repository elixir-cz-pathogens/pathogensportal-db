"""Scraper RespiCast — bez sítě; hlídá filtr na ČR, zdvojený medián a cache kol."""
from datetime import date, timedelta

import pandas as pd
import pytest

from scrapers import ecdc_respicast as rc

HEADER = "origin_date,target,target_end_date,horizon,location,output_type,output_type_id,value\n"


def _round_csv(origin: str) -> bytes:
    rows = [
        f'{origin},"ILI incidence",2026-09-20,2,"CZ","median","",6.1',
        f'{origin},"ILI incidence",2026-09-20,2,"CZ","quantile","0.5",6.1',
        f'{origin},"ILI incidence",2026-09-20,2,"CZ","quantile","0.975",16.0',
        f'{origin},"ILI incidence",2026-09-20,2,"FI","quantile","0.5",99.0',
        f'{origin},"COVID-19 cases",2026-09-20,2,"CZ","quantile","0.5",1.0',
    ]
    return (HEADER + "\n".join(rows) + "\n").encode()


class _Resp:
    def __init__(self, content=b"", payload=None):
        self.content, self._payload = content, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _serve(monkeypatch, origins, calls):
    def fake_get(url, **kwargs):
        calls.append(url)
        if url == rc.LISTING_URL:
            return _Resp(payload=[{"name": f"{o}-{rc.MODEL}.csv"} for o in origins] + [{"name": "Readme.md"}])
        return _Resp(content=_round_csv(url.rsplit("/", 1)[1][:10]))
    monkeypatch.setattr(rc.requests, "get", fake_get)


def test_keeps_czech_quantiles_only_and_caches_rounds(monkeypatch, tmp_path):
    recent = (date.today() - timedelta(days=3)).isoformat()
    calls = []
    _serve(monkeypatch, ["2025-01-08", recent], calls)

    out = pd.read_csv(rc.download(tmp_path)[0])
    assert set(out["ukazatel"]) == {"ILI"} and len(out) == 4          # 2 kola × 2 kvantily, bez "median"
    assert sorted(out["kvantil"].unique()) == [0.5, 0.975]

    calls.clear()
    rc.download(tmp_path)
    assert calls == [rc.LISTING_URL]                                   # kola z cache, stahuje se jen výpis


def test_fails_when_hub_stops_publishing(monkeypatch, tmp_path):
    _serve(monkeypatch, ["2025-01-08"], [])
    with pytest.raises(ValueError, match="přestal přibývat"):
        rc.download(tmp_path)

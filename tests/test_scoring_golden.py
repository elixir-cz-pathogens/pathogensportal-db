"""Golden test skórování — chrání jádro před tichou změnou chování.

Deterministická syntetická řada; očekávané hodnoty byly spočítány verzí,
u které GLM jádro prošlo srovnáním se statsmodels (test_glm). Kdo změní
metodu, MUSÍ vědomě přegenerovat golden hodnoty a zdůvodnit to v commitu.
"""
import numpy as np

from detect_anomalies import farrington_score, apply_fdr


def _series():
    # 8 let, sezónní řada s vrcholem v zimě, deterministická (bez šumu),
    # poslední měsíc zvednutý na trojnásobek sezónního normálu.
    months = np.arange(96)
    base = 20 + 10 * np.cos(2 * np.pi * (months % 12) / 12)
    counts = np.round(base).astype(float)
    counts[95] = round(base[95] * 3)
    return counts


def test_golden_signal():
    rng = np.random.default_rng(20260906)
    res = farrington_score(_series(), 95, rng=rng)
    assert res["type"] == "glm"
    assert res["signal"] is True
    assert res["observed"] == 86.0
    # Golden hodnoty změřené verzí, jejíž GLM prošlo srovnáním se statsmodels.
    # Malá tolerance kryje přeuspořádání floatových operací, ne změnu metody.
    assert abs(res["expected"] - 29.0) < 0.01
    assert abs(res["threshold"] - 43.37) < 0.05
    assert abs(res["score"] - 3.97) < 0.05
    assert res["pi"] > 0.999


def test_golden_quiet_month():
    counts = _series()
    counts[95] = counts[83]  # poslední měsíc = běžná sezónní hodnota
    rng = np.random.default_rng(20260906)
    res = farrington_score(counts, 95, rng=rng)
    assert res["signal"] is False
    assert res["pi"] < 0.5


def test_fdr_marks_top_k():
    recs = [{"pi": p} for p in (0.999, 0.995, 0.9, 0.5, 0.1)]
    k, k_bh = apply_fdr(recs, alpha=0.10)
    assert k == 3  # (0.001+0.005+0.1)/3 ≈ 0.035 ≤ 0.10; přidat 0.5 už to zlomí
    assert [r["fdr_pass"] for r in recs] == [True, True, True, False, False]

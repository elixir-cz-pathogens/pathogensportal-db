# Seasonal influenza thresholds, trend and forecast

`scripts/compute_mem.py` writes `$OUTPUT_DIR/flu_mem.json`. For two indicators it answers three
questions: has the season started and how intense is it, is the wave growing or declining, and
what is expected in the coming weeks.

```bash
python scripts/compute_mem.py
python scripts/compute_mem.py --delta 3.0
python scripts/compute_mem.py --optimize-delta     # choose δ by the Youden index
```

| Module | Role |
|---|---|
| `compute_mem.py` | reads the CSVs, builds the week × season matrix, selects seasons, writes the output |
| `mem.py` | the Moving Epidemic Method — pure computation, no I/O |
| `trend.py` | growth rate and trend category — pure computation |
| `forecast.py` | threshold exceedance probability and forecast scoring — pure computation |

## Input

Both indicators are weekly rates per 100,000 inhabitants from the reports of general
practitioners.

| Indicator | Definition | Behaviour |
|---|---|---|
| **ILI** — influenza-like illness | sudden onset + fever + a respiratory symptom; narrow, close to real influenza | a wave lifts it about 15× above the autumn baseline — the main intensity indicator |
| **ARI** — acute respiratory infection | any respiratory symptom: rhinoviruses, RSV, COVID-19 and influenza together | a wave lifts it only about 2×; but it is the number Czech public health traditionally speaks in |

The history comes from **WHO FluID** (continuous since 2009/10), the most recent weeks from
**ECDC ERVISS**. It is the same series reported through two routes — for ILI, 205 common weeks
with a largest difference of 1.4 % — and ERVISS simply arrives earlier. Where ERVISS has a week,
its rate is used; case counts and the population covered are known only from FluID.

Laboratory detections are not suitable as an input: with the growth of testing they rose by
orders of magnitude (about 1,000 specimens tested in 2019, about 64,500 in 2024), so a threshold
estimated from past seasons would be permanently exceeded today.

A surveillance season runs from ISO week 40 to week 20 of the following year (the ECDC
convention). Week 53, which exists only in some years, is averaged with week 52 so that all
seasons have the same length.

## Thresholds — the Moving Epidemic Method

MEM (Vega et al. 2013 and 2015) is the standard of ECDC and WHO PISA for influenza surveillance.
`mem.py` is an independent numpy reimplementation of the R package `mem`, with the same default
parameters.

1. **Epidemic period of each past season.** For every length *r*, take the *r* consecutive weeks
   with the largest sum and compute the share of the whole season they cover. The curve rises and
   flattens; the epidemic has the length of the last *r* where one more week still added at least
   δ percent of the season. The increments are smoothed first, otherwise one jagged week would
   decide the length.
2. **Epidemic threshold** ("the season has started"). From the weeks *before* the epidemic, the
   *n* highest values of each season are taken (*n* ≈ 30 / number of seasons). The threshold is the
   upper limit of the one-sided 95 % interval — a value a quiet week exceeds in only 5 % of cases.
3. **Intensity thresholds.** The same from the *n* highest weeks *during* the epidemic, on the
   logarithmic scale, at the 40 %, 90 % and 97.5 % levels → medium, high, very high.

The current week is then classified as `baseline`, `low`, `medium`, `high` or `very_high`.

One difference from the R package: missing weeks inside a season are interpolated linearly, where
the package uses kernel smoothing. For single gaps the difference is negligible, and the linear
version is deterministic and explainable.

### Which seasons are used

The last 10 valid seasons. A season is left out when it is:

- **the running season**, or incomplete;
- **excluded by hand** in `EXCLUDED_SEASONS`: the 2009/10 pandemic, the COVID seasons 2020/21 and
  2021/22, and 2025/26, where the population covered in FluID jumped from 5.4 to 8.5 million and
  the change is not yet verified;
- **a season without an epidemic** — detected automatically: its peak is below the epidemic
  threshold computed from the other seasons. Today this is only 2013/14. The data is complete;
  influenza simply hardly circulated that winter. Left in, that one season would raise the "high"
  threshold above everything ever measured.

Every exclusion is written into the output together with its reason.

### The δ parameter

δ is fixed at the standard 2.8 — the default of the R package and the value ECDC uses, so the
thresholds are comparable with other countries. An optimisation by leave-one-season-out exists
(`--optimize-delta`) but only on request: with ten seasons the Youden curve is jagged and the
differences are within noise. The sensitivity to δ is always written into the output
(`delta_sensitivity`), so it is visible what the decision rests on.

### Why ARI has no intensity bands

The output carries `intensity_reliable` for each indicator: `true` when the Youden index of the
leave-one-season-out validation is at least 0.7.

- **ILI:** the back-test catches 92 % of epidemic weeks. Reliable.
- **ARI:** the influenza wave is lost in the year-round background of other viruses; only 47 % of
  weeks are caught. Not reliable.

For ARI the portal therefore draws only the curve with the epidemic threshold, without intensity
bands that would claim a precision the data does not have.

## Trend

`trend.py` follows the CDC "Current Epidemic Trends": instead of a number, show a category derived
from the probability of growth.

It uses the growth rate, not Rt. A reproduction number belongs to one pathogen with a known
generation interval; ILI and ARI are syndromes — a mix of influenza, RSV, COVID-19 and
rhinoviruses.

Method: weighted linear regression of log(rate) on time over a moving window of **3 weeks**, the
weight being the number of cases. The probability of growth is P(slope > 0) from Student's t
distribution.

| `p_growth` | Category |
|---|---|
| ≥ 0.90 | `growing` |
| ≥ 0.75 | `likely_growing` |
| ≥ 0.25 | `stable` |
| ≥ 0.10 | `likely_declining` |
| < 0.10 | `declining` |

The window length was decided by a back-test: with three weeks the categories are correctly
ordered (after "growing" the ILI rate rose the following week in 69 % of cases, after "declining"
in 11 %), while longer windows lose the order and keep reporting growth two weeks after the peak.
The current back-test figures are always in the output.

Two safeguards:

- Around Christmas (weeks 52, 53 and 1) surgeries are closed and reports drop regardless of the
  epidemic. The trend is still computed but carries `holiday_effect: true`.
- ILI and ARI are not reported over the summer. Until three consecutive weeks exist after the
  break, there is no trend.

## Forecast

The forecast is the ensemble of the ECDC **RespiCast** hub for four target weeks, downloaded by
`ecdc_respicast.py`. It predicts the same series the thresholds are computed on, so it can be
compared with them directly.

- A hub publishes quantiles, not a distribution. `forecast.prob_at_least()` turns them into the
  **probability of exceeding our epidemic threshold** (`p_epidemic`), clipped to 1–99 % — outside
  the published quantiles the forecast says nothing.
- The lead is computed from the dates, not from the hub's `horizon` column, because the hub
  changed its horizon numbering between seasons. Lead 0 is the week that has just ended and has no
  consolidated data yet; leads 1–3 are the outlook.
- Past forecasts for Czechia are **scored against what happened**: the weighted interval score
  relative to the hub's reference model (below 1 = better than "next week will be like this one";
  ILI 0.85) and the real coverage of the intervals (the "95 %" band caught 85 %).

The forecast is shown as it is, next to its historical performance. Stretching the intervals was
tried and rejected: a scale estimated on one season did not carry over to the next.

## Output

```jsonc
{
  "method": "Moving Epidemic Method (Vega et al. 2013, 2015)",
  "generated_at": "2026-09-24T06:04:31Z",
  "season_weeks": [40, 41, "…", 20],
  "indicators": {
    "ili": {
      "label": "ILI na 100 tis. obyvatel",
      "intensity_reliable": true,
      "sources": ["ECDC ERVISS", "WHO FluID"],
      "delta": 2.8,
      "seasons_used": ["2012/13", "…"],
      "seasons_excluded": { "2009/10": "reason…", "2013/14": "reason…" },
      "thresholds": { "epidemic": 0, "medium": 0, "high": 0, "very_high": 0 },
      "epidemic_periods": { "2012/13": { "start_week": 3, "end_week": 11 } },
      "validation": { "…": "sensitivity, specificity, youden at the δ used" },
      "delta_sensitivity": [ { "delta": 2.0, "…": "" } ],
      "current": { "season": "2026/27", "week": "2026-W38", "value": 0, "level": "baseline",
                   "epidemic_week": null, "source": "ECDC ERVISS" },
      "trend": { "window_weeks": 3, "current": { "category": "stable", "p_growth": 0.5,
                 "weekly_change": 0.0, "doubling_weeks": null, "holiday_effect": false },
                 "by_season": { "…": [] }, "backtest": { "growing": { "n": 0, "next_week_up": 0 } } },
      "forecast": { "source": "ECDC RespiCast — ensemble hubu",
                    "latest": { "round": "2026-09-16", "weeks": [ { "week": "2026-W39", "lead": 1,
                                "q025": 0, "q250": 0, "q500": 0, "q750": 0, "q975": 0, "p_epidemic": 0 } ] },
                    "by_last_observed_week": { "…": {} },
                    "evaluation": { "overall": { "n": 0, "relative_wis": 0, "coverage_50": 0, "coverage_95": 0 },
                                    "by_lead": {}, "by_season": {} } },
      "history": { "2012/13": [0, 0, "…"] }
    },
    "ari": { "…": "same structure" }
  },
  "meta": { "…": "see ../metadata.md" }
}
```

The zeros stand for real values. `forecast` is `null` when no RespiCast file is available, and the
whole file is skipped when `who_fluid_cz.csv` is missing.

## Validation

- `tests/test_mem_golden.py` holds `mem.py` to the R package `mem` 2.19 to 8+ significant digits —
  thresholds and the starts and ends of epidemics, on the Czech ILI data and on synthetic seasons
  that exercise paths the Czech data never takes. The reference numbers were produced once by
  running R; R is not needed in CI.
- `tests/test_compute_mem.py` covers the week × season matrix (season boundary, week 53).
- `tests/test_trend.py` covers the categories, the certainty and the edge cases.
- `tests/test_forecast.py` covers the exceedance probability.

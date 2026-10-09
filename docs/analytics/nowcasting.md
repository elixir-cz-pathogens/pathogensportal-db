# Nowcasting laboratory detections

`scripts/compute_nowcast.py` writes `$OUTPUT_DIR/flu_nowcast.json`: an estimate of the final number
of laboratory detections for the most recent weeks, which laboratories are still reporting.

**Status: shadow mode** (since October 2026). The estimate is computed every run and logged, but
the portal does not show it (`"display": false`). Whether and how to show it is decided once 8–10
weeks of the 2026/27 season have been scored — see [Decision rule](#decision-rule).

```bash
python scripts/compute_nowcast.py
python scripts/compute_nowcast.py --as-of 2026-11-20    # as if run on that day
```

| Module | Role |
|---|---|
| `scrapers/ecdc_erviss_snapshots.py` | mirrors the Czech rows of the weekly ERVISS snapshots |
| `nowcast.py` | reporting triangle, chain-ladder, uncertainty, backtest — pure computation, no I/O |
| `compute_nowcast.py` | reads the mirror and the SZÚ matrix, writes the output and the shadow log |

## The problem

The number of detections for a week keeps growing for 2–3 weeks after it is first published,
because laboratories report late. The last points of the weekly chart (`flu_weekly`) therefore
always drop, and at the start of a wave that looks like a slowdown.

Measured on 129 weekly ERVISS snapshots (November 2023 – October 2026), as the median share of
the final count:

| Series | First publication | One week later | Settled after |
|---|---|---|---|
| Influenza | 0.78 | 1.00 (10th percentile 0.72) | 2–3 weeks |
| RSV | 0.73 | 1.00 (10th percentile 0.75) | 2 weeks |
| SARS-CoV-2 | 0.98 | 0.99 | — |

What does **not** need a nowcast, and why:

- **ILI/ARI rates** (the MEM input): never revised after publication (0 % of weeks changed by
  more than 0.5 %). They are only published late, and a week not yet published is a forecasting
  problem, not a nowcasting one.
- **Positivity**: not biased (median difference from the final value 0 percentage points), only
  noisier in the first week (90th percentile of the absolute difference 1–1.5 p.p.).
- **SARS-CoV-2 detections**: complete at first publication.

## Data

- **Reporting delays** come from the ERVISS snapshots. ECDC stores the whole file every Friday in
  `data/snapshots/` of its GitHub repository; the scraper keeps the Czech rows of
  `nonSentinelTestsDetections`. Our own archive (`raw/`) only started in September 2026.
- **Reported numbers** come from `szu/szu_weekly_viry.csv` — exactly what the chart shows.
- ERVISS non-sentinel detections are the SZÚ numbers. Checked on 9 October 2026 against the SZÚ
  matrix from 2024/25 on: influenza A 82 of 84 weeks identical (totals 7,636 vs 7,633),
  influenza B 45 of 46, RSV 79 of 81; the largest weekly difference is 3 cases.
- Since 2024 ERVISS gives no total per influenza type, only subtypes; the type total is the sum
  of all subtypes.

## Method

Notation: N(t, d) is the count for week t as known d weeks after the week ended
(d = 0 is the first Friday after the week). A week is final at D = 8.

1. **Completion factors (chain-ladder).** Over the last 52 weeks known at the time,
   f_d = Σ N(t, d+1) / Σ N(t, d), over weeks where both are known. A ratio of sums, not a mean of
   ratios — more stable with small counts. F_d = f_d · f_{d+1} · … · f_7.
2. **Point estimate.** N(t, d) · F_d.
3. **Uncertainty.** The remainder to be reported is drawn from a negative binomial distribution
   with mean N(t, d) · (F_d − 1). Its dispersion φ_d is estimated by the method of moments from
   weeks whose final value is already known. 2,000 draws give quantiles 2.5, 10, 25, 50, 75, 90
   and 97.5 %. The estimate is never below the reported number.

**Scope:** only delays 0 and 1, only in season (ISO weeks 40–20), only influenza A, influenza B
and RSV. For older weeks and outside the season the backtest showed no gain, sometimes a loss:
the data are nearly complete, or the counts are single cases.

The random seed is fixed, so hourly runs on the same data produce the same file.

## Backtest

Run as in real time: at every snapshot, only the data known that day; the estimate for weeks
with delay 0–1 in season is compared with the final count. The alternative is "do nothing" —
show the reported number; its WIS is the absolute error. Seasons 2024/25 and 2025/26:

| Series | n | WIS: lower than "do nothing" by | 95 % band contained the outcome |
|---|---|---|---|
| Influenza A | 61 | 27 % | 89 % |
| Influenza B | 38 | 7 % | 82 % |
| RSV | 63 | 50 % | 89 % |

By season the point estimate improves in both seasons, but the 95 % band is too narrow in
2024/25 (coverage 0.73–0.79) and too wide in 2025/26 (1.00). Partly this is because in 2024/25
the model had less than a year of history. Influenza B gains little in a strong season and loses
in a weak one (mean 1.5 cases a week in 2025/26).

The backtest is recomputed on every run and written to `series.*.backtest`.

## Shadow mode

Every estimate goes to `$DATA_DIR/nowcast/flu_nowcast_log.csv`, one row per series, week and
delay; the last estimate of the day wins. Once a week is 8 weeks old, its estimate is compared
with the count SZÚ has reported by then, and `shadow_evaluation` in the output holds, per series:
`n`, WIS of the estimate and of the reported number, the improvement, the coverage of the 50 %
and 95 % bands, and `pending` (estimates not yet scored).

One open question the shadow mode answers: the factors are learned from ERVISS snapshots
(Fridays), while the SZÚ PDF comes out earlier in the week. If the first SZÚ number is less
complete than the first ERVISS number, the estimate will be biased low — the shadow evaluation
shows it, and the factors can then be learned from our own SZÚ archive instead.

## Decision rule

A week can be scored only once it is 8 weeks old: the first in-season week (KT 40/2026) is scored
from 30 November 2026, and 8–10 scored weeks per delay are available around the end of January
2027. Then, per series:

| Shadow evaluation | On the portal |
|---|---|
| WIS clearly lower and 95 % coverage 0.90–0.98 | dashed estimate and the band |
| WIS lower, coverage outside 0.90–0.98 | dashed estimate only, labelled as indicative |
| WIS not lower | nothing; the chart stays as it is |

## Output

```json
{
  "status": "shadow", "display": false, "as_of": "2026-10-09", "in_season": true,
  "levels": [0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975],
  "params": {"d_final": 8, "window_weeks": 52, "delays": [0, 1]},
  "training_snapshots": {"n": 129, "first": "2023-11-24", "last": "2026-10-09"},
  "series": {
    "Influenza A (celkem)": {
      "weeks": [{"week": "2026-09-28", "label": "KT 40/26", "delay": 0, "reported": 1,
                 "quantiles": {"0.025": 1.0, "0.5": 1.0, "0.975": 3.0}}],
      "model": {"factors": {"0": 1.316, "1": 1.051}, "dispersion": {"0": 28.6, "1": 0.5},
                "training_weeks": {"0": 18, "1": 21}},
      "backtest": {"all": {"n": 61, "improvement": 0.265, "coverage_95": 0.885}, "by_delay": {}}
    }
  },
  "shadow_evaluation": {"Influenza A (celkem)": {"n": 0, "pending": 1}}
}
```

Series names match the dataset labels in `flu_weekly.json`, so the portal can pair them.

## Failure handling

The step must not stop the data refresh: in the container command it follows `||`, so if it
fails the charts are still generated and only `flu_nowcast.json` is not updated. The snapshot
mirror is an optional job in `run_all.py` — its failure does not count towards the exit code.
On the server, the first fill of the mirror (about 1 GB) runs in batches of 40 snapshots per run.

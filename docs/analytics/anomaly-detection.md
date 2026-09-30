# Anomaly detection — the early-warning system on ISIN

`scripts/detect_anomalies.py` writes `$OUTPUT_DIR/anomaly_signals.json`, which feeds the portal's
Signals page.

ISIN provides well over a thousand monthly time series (114 diagnoses × 14 regions, plus the
whole country) that nobody watches by eye. For each of them the script computes the expected
endemic level and flags the last month when the reported count exceeds a threshold.

```bash
python scripts/detect_anomalies.py                     # score the last month, write the JSON
python scripts/detect_anomalies.py --as-of 2026-09-01  # run on an archived snapshot, bit-identical output
python scripts/detect_anomalies.py --alpha 0.05        # level of the Bayesian FDR
python scripts/detect_anomalies.py --backtest          # score the whole history into a CSV
python scripts/detect_anomalies.py --backtest --diagnoza "Dávivý kašel [pertussis]"
```

## Method

The method is the Farrington algorithm (Farrington et al. 1996) with the improvements of Noufaily
et al. 2012 — the procedure UKHSA runs every week over thousands of laboratory report series; its
reference implementation is the R package `surveillance`. Here it is reimplemented in plain numpy:
the GLM is small and IRLS is a few lines, so the pipeline does not need a heavy dependency.

How one month of one series is scored:

1. **Baseline.** Two variants, chosen with `--model`:
   - `plny` (full, the default; Noufaily 2012) — the whole history except the last 6 months, with
     seasonality handled by a calendar-month factor. It uses about four times more data. When
     there are fewer than 42 baseline points for its 13 parameters, it falls back to windows.
   - `okna` (windows; Farrington 1996) — the same calendar month ±1 in previous years;
     seasonality is handled by the choice of data.
2. **Model.** A quasi-Poisson GLM with a log link and a linear trend. "Quasi" because real counts
   vary more than Poisson allows, and underestimating the variance means false alarms. The trend
   is kept only when it is significant and does not overshoot (Farrington's rule).
3. **Re-weighting.** Outlying baseline points (Anscombe residual beyond 2.58) are down-weighted and
   the model is refitted once. Otherwise the system would learn that last year's epidemic is
   normal and stay silent this year. Unlike Noufaily, the re-weighting is symmetric: the COVID
   years *suppressed* counts, and a one-sided rule lets that depressed baseline pull the
   expectation down, so the return to normal looks like an epidemic.
4. **Threshold.** The upper bound of the prediction interval on the 2/3 power scale (more stable
   for small counts), at the 99th percentile. The threshold never goes below one case.
5. **Signal.** An observation above the threshold with at least 3 cases. Strength is reported as
   the exceedance score `(observed − expected) / (threshold − expected)`; a signal has a score
   above 1.

Series where a GLM makes no sense get a rule instead:

| Type | When | Rule |
|---|---|---|
| `rare` | the whole history sums to at most 5 cases (measles, diphtheria…) | 2 or more cases in the month is a signal — a single import is common |
| `sporadic` | there are cases in the history but none in the baseline windows | 3 or more cases is a signal |
| `glm` | everything else | the model above |

A series with less than 3 years of usable history is not scored.

### Parameters

All are constants at the top of the script and are copied into the output's `provenance` block.

| Constant | Value | Meaning |
|---|---|---|
| `Z_QUANTILE` | 2.326 | 99th percentile — about one false alarm per 100 quiet months |
| `REWEIGHT_LIMIT` | 2.58 | Anscombe residual from which a baseline point is down-weighted |
| `HALF_WINDOW` | 1 | ±1 month around the same calendar month (windows model) |
| `MIN_YEARS` | 3 | less history than this → the series is not scored |
| `MIN_CASES_GLM` | 3 | a GLM signal is not reported below 3 cases |
| `RARE_TOTAL` / `RARE_ALERT` | 5 / 2 | definition and alert level of a rare series |
| `RECENT_SKIP` | 6 | full model: months left out of the baseline, so that a starting epidemic does not raise its own threshold |
| `FULL_MIN_POINTS` | 42 | minimum baseline points for the full model |
| `PI_DRAWS` / `PI_SEED` | 500 / 20260906 | bootstrap draws and the fixed seed |
| `FDR_ALPHA` | 0.10 | default level of the Bayesian FDR |

## From one series to a thousand: π and FDR

The binary rule "observed > threshold" pretends the threshold is known exactly, although it is
estimated from a finite history. So for every scored series the script also computes **π** — the
probability that the observation exceeds the threshold given the uncertainty of the estimated
parameters — by a parametric bootstrap (500 draws of the coefficients, fixed seed).

With more than a thousand series tested at once, some will cross the threshold by chance. The
script therefore applies a **Bayesian false discovery rate**: it sorts the series by π and marks
the largest set whose expected share of false signals does not exceed α (default 0.10). Each
signal carries `fdr_pass`. The Benjamini–Hochberg count is reported next to it for comparison.

## Registry of methodology changes

A change in how cases are reported looks exactly like an epidemic, and without knowing about it
the detector reports it as an outbreak. One such false detection discredits the tool with
epidemiologists for good. `methodology_changes.yaml` is therefore a blocking part of the system,
not a documentation aid.

```yaml
- id: zoster-vykazovani-2025-07
  od: 2025-07            # from
  do: null               # until; null = still in effect
  akce: break            # action
  rozsah:                # scope
    diagnozy: ["Pásový opar [herpes zoster]"]
    kraje: null          # null = all regions
  zdroj: "…"             # how we know
  popis: "…"             # what happened
  dopad: "…"             # expected effect on the counts
```

| `akce` | Effect on the detector |
|---|---|
| `break` | the level of the series changed: for months from `od` on, everything before `od` is dropped from the baseline. Until enough new history accumulates the series is not scored and is listed under `prebaselining` |
| `exclude` | the `od`–`do` period is cut out of the baseline (a temporary distortion such as a reporting outage) |
| `flag` | nothing changes; signals in the period carry a note |
| `poznamka` | informational record, no effect on the computation |

A signal that falls into the period of an active record carries the record's id in
`metodicka_zmena`. The same registry supplies the `caveats` in the chart metadata — see
[../metadata.md](../metadata.md).

## The EWS reporting channel

Since July 2025 ÚZIS also accepts cases through EWS reports, and the `EWS` column in the source
data says how many cases arrived that way — more than 25 thousand in the second half of 2025;
62 % of herpes zoster cases, 59 % of Lyme disease, 51 % of mononucleosis. The series rise without
more people being ill.

Subtracting the EWS cases is not enough, because part of the reports *moved* from the old channel
to the new one (zoster without EWS dropped from about 340 to about 175 a month). The count that is
comparable with history is therefore unknown; it lies somewhere between "reported − EWS" and
"reported". The detector decides for each signal accordingly:

- if even the lower bound (reported − EWS) is above the threshold, the signal holds whatever moved
  between channels — `reporting_channel.robust = true`;
- otherwise the signal is **undecidable**: the increase may be nothing more than the new channel.

Undecidable signals are sorted below the decidable ones in the output. In December 2025, 56
signals split into 24 decidable and 32 undecidable.

## Reproducibility

`--as-of YYYY-MM-DD` runs the detector on the archived ISIN file that was valid on that date
(the newest snapshot not later than the date, see [../snapshots.md](../snapshots.md)) instead of
the live CSV. With the fixed bootstrap seed the output is bit-identical on every re-run.

Every output carries a `provenance` block: the input path and its sha256, the `as_of` date, the
code version (`git describe`; `unknown` inside the Docker image, which has no `.git`) and all
parameters.

## Output

```jsonc
{
  "generated_at": "2026-09-24T06:04:10Z",
  "target_period": "2025-12",
  "method": "Farrington–Noufaily, kvazi-Poisson GLM, 99. percentil, π parametrickým bootstrapem, bayesovská FDR",
  "n_series_scored": 1180, "n_series_skipped": 412,
  "n_signals": 56, "n_signals_undecided": 32,
  "fdr": { "alpha": 0.1, "n_tested": 1180, "n_pass": 41, "n_pass_bh": 38 },
  "prebaselining": [ { "diagnoza_nazev": "…", "kraj_nazev": "…", "observed": 1260.0,
                       "zmena": "zoster-vykazovani-2025-07", "od": "2025-07" } ],
  "signals": [
    { "diagnoza": "A37", "diagnoza_nazev": "Dávivý kašel [pertussis]",
      "kraj_kod": "CZ064", "kraj_nazev": "Jihomoravský kraj",
      "type": "glm", "observed": 48.0, "expected": 6.2, "threshold": 14.9, "score": 4.8,
      "pi": 0.998, "fdr_pass": true, "n_baseline": 84,
      "metodicka_zmena": "…",
      "reporting_channel": { "ews": 5.0, "share": 0.1, "lower_bound": 43.0, "robust": true } }
  ],
  "provenance": { "input": "…", "input_sha256": "…", "as_of": null, "code_version": "v0.4.0-12-g199f76f",
                  "params": { "…": "" } },
  "meta": { "…": "see ../metadata.md" }
}
```

The numbers in this example are illustrative. `metodicka_zmena` and `reporting_channel` are
present only when they apply. Region `CZ` is the national total, which includes cases with no
region stated; the "not stated" region itself is not scored.

`--backtest` writes `$DATA_DIR/analysis/anomaly_backtest.csv` with one row per series and month
instead of the JSON.

## Validation

- **Back-test on pertussis 2024.** The first signal came 3 months before the epidemic became a
  public topic, with no false alarm in the quiet period. This episode was used during development,
  so catching it is a necessary condition, not proof of quality.
- **Simulation study** (`scripts/simulate_detection.py`). On real data neither sensitivity nor
  the false alarm rate can be measured, because no list of all true epidemics exists. The study
  generates synthetic seasonal negative-binomial series, injects outbreaks of known size and
  measures what the detector catches — the way Noufaily et al. validated the method. Detection of
  an outbreak of 3σ / 5σ / 10σ: 27 / 60 / 96 %; false alarms 1.5–3 %.

  ```bash
  python scripts/simulate_detection.py            # full study, about 2 minutes
  python scripts/simulate_detection.py --rychla   # quick, about 4× fewer replications
  ```

  Results go to stdout and to `$DATA_DIR/analysis/simulation_results.csv`.
- **Tests.** `tests/test_glm.py` compares the GLM core with `statsmodels` (skipped when
  `statsmodels` is not installed). `tests/test_scoring_golden.py` pins the scoring of a
  deterministic synthetic series; whoever changes the method must regenerate the golden values
  deliberately and justify it in the commit. `tests/test_reporting_channel.py` covers the EWS
  verdict.
- **Holdout.** Three documented episodes are frozen and must not be used during development — see
  [../HOLDOUT.md](../HOLDOUT.md).

## Limits

- The open ISIN export currently ends in December 2025. The "last month" the detector scores is
  therefore old; silence in later periods is missing data, not calm.
- The data is monthly. The detector cannot be faster than the reporting.

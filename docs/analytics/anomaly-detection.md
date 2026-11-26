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
python scripts/detect_anomalies.py --zdroj tydenni     # 11 diagnoses up to the last complete month
```

`--zdroj tydenni` is a second, smaller run on another source; it writes
`anomaly_signals_weekly.json` and leaves `anomaly_signals.json` alone. See
[The weekly source](#the-weekly-source-eleven-diagnoses-up-to-the-last-complete-month).

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

## The weekly source: eleven diagnoses up to the last complete month

The open ISIN export is refreshed once a year, so the main run scores December of the previous
year for most of the following one. The weekly ÚZIS data summary (`scrapers/uzis_isin_weekly.py`)
covers only 11 diagnoses, but up to the previous week and with the EWS records counted
separately. `--zdroj tydenni` runs the same detector on it — same model, same parameters, same
EWS verdict — and writes `anomaly_signals_weekly.json` for the last complete calendar month.

**The whole series comes from the weekly source**, history included (2022 onwards); the weekly
months are not appended to the open CSV. Three reasons:

- A month computed from weeks is an estimate. `weekly_months.py` splits a week that crosses two
  months by working days (Monday to Friday without public holidays), in whole cases, so the weekly
  total is preserved. Measured against the open CSV on seven diagnoses with the same code
  (Czechia, 2/2022–12/2025, months with 20+ cases): median difference 1.6 %, nine months in ten
  within 3.9 %. Splitting by seven calendar days gives 4.4 % / 8.7 %, assigning the week to the
  month of its Thursday 7 % / 17 %. Used on both the history and the scored month, the error is
  the same on both sides of the comparison.
- The summary has narrower codes for three diagnoses (A04.5, A37.0, A69.2 against A04, A37, A69
  in the open CSV); A04.5 is about two thirds of A04.
- The national series is the summary's own sheet for Czechia, not the sum of the regions: from
  March 2026 on, 1–10 % of the records of a diagnosis carry no region (registry record
  `isin-tydenni-kraje-bez-kraje`). A regional signal just above the threshold can therefore be
  missed; a false one cannot arise this way.

**What EWS did to each diagnosis** (`classify_weekly`, recomputed for every scored month). The
total since July 2025 is compared with the median of the same calendar months before it:

| Group | Rule | November 2026 | Treatment |
|---|---|---|---|
| `presun` (reports moved) | level ≤ 1.5× | salmonellosis 0.90, campylobacteriosis 0.96, chickenpox 0.96, whooping cough 1.05, mumps 1.29 | scored on the total, as before; months with EWS stay in the baseline; no EWS verdict |
| `novy_kanal` (new channel) | level > 1.5× and ≥ 50 % from EWS | Lyme disease 4.33, shingles 4.54, mononucleosis 3.26, scabies 1.66 | not scored — the series measures something else than before; status `zmena_hlaseni` |
| `nejiste` (uncertain) | level > 1.5×, EWS a minority | hepatitis A 23 (epidemic), measles 3.2 (few cases) | baseline before July 2025, EWS verdict as in the main run |
| `bez_zmeny` | EWS < 10 % in the last 12 months | — | as in the main run |

Why not one rule for all: for chickenpox, 86 % of the records now come from EWS, yet the total did
not rise (weeks 27–45: 6,081 in 2024, 5,409 in 2026) — the old channel simply emptied (706). The
subtraction "reported − EWS" would make every chickenpox month undecidable for nothing. For
shingles the total rose 5.4× while the old channel fell to a third: EWS reaches cases that were
never reported, and no conversion turns them back into the old units.

**Epidemic months** before July 2025 — national count above 4× the median of the same calendar
month and at least 10 cases — are left out of the baseline of every series of the diagnosis and
out of the usual range. Reweighting outliers is not enough when an epidemic fills a third of a
short history: with the 2024 pertussis epidemic in it, the October 2026 limit was 7,277 a month;
without it, 238.

**Output.** Besides `signals`, `national` holds every diagnosis with its group, status (`v_norme`,
`signal`, `nelze_rozhodnout`, `zmena_hlaseni`, `nehodnoceno`) and the last 12 months with the usual
range (min–max of the same calendar month before July 2025, epidemic months excluded) for the
portal's tiles. October 2026: one diagnosis above the usual level (salmonellosis — nationally,
Prague, Central Bohemia, Liberec), six within it, hepatitis A not scored (too little history after
leaving out the epidemic), four `zmena_hlaseni`.

**Backtest on 2025** (before the grouping above was added; nine diagnoses, 15 areas, 12 months — 1,560 verdicts on the series present
in both sources; the weekly series against the open CSV cut to the same history from 2022):
97.4 % of the verdicts agree. The 40 that
differ lie near the threshold (median distance 8 %). Shortening the history from 2018 to 2022 on
the open CSV itself changes more — 94.4 % agreement.

**Limits of this run.**

- The history is short (from 2022). For the `novy_kanal` and `nejiste` groups it does not grow while
  the rule holds; hepatitis A is not scored at all until enough non-epidemic history accumulates.
- The level ratio is a coarse instrument: an epidemic lasting most of the period since July 2025
  would raise it as well. For a disease with EWS in the majority it would then be classified as
  `novy_kanal` and not scored.
- The summary carries the last five years. The scraper keeps a year that drops out of it from the
  previous download (`keep_older_years`), so the history does not shrink — but only on a server
  that keeps its `$DATA_DIR`. A fresh data directory starts again with five years.
- `--as-of` is not supported; the run uses the last downloaded summary.

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
present only when they apply. `method_en`, and `diagnoza_nazev_en` / `kraj_nazev_en` in every
signal and `prebaselining` record, carry the English names looked up by the ICD-10 and NUTS3 codes
(see [chart-generation.md](../chart-generation.md#english-labels)). Region `CZ` is the national total, which includes cases with no
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
  verdict, `tests/test_weekly_months.py` the weekly source.
- **Holdout.** Three documented episodes are frozen and must not be used during development — see
  [../HOLDOUT.md](../HOLDOUT.md).

## Limits

- The open ISIN export currently ends in December 2025. The "last month" the detector scores is
  therefore old; silence in later periods is missing data, not calm.
- The data is monthly. The detector cannot be faster than the reporting.
- The weekly source reaches the last complete month, but only for 11 diagnoses and with the
  limits listed in its own section.

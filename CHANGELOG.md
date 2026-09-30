# Changelog

Releases are git tags `vMAJOR.MINOR.PATCH`. The portal pins one of them as its submodule; see
[docs/deployment.md](docs/deployment.md). Changes that break the
[contract with the portal](README.md#contract-with-the-portal) are marked **Breaking**.

## Unreleased

Changes on `dev` since v0.4.0.

- **WHO FluNet / FluID and ECDC ERVISS scrapers** (PPDB-60) — weekly influenza laboratory
  detections with the number of specimens tested, and ILI/ARI rates; loaders and tests.
- **Seasonal influenza thresholds** (PPDB-61) — Moving Epidemic Method on ILI and ARI, golden tests
  against the R package `mem`; seasons without an epidemic are excluded from the data; δ fixed at
  2.8. New output `flu_mem.json`, and `compute_mem.py` added to the container command.
- **RespiCast forecast** (PPDB-62) — scraper of the ECDC hub forecasts for Czechia, probability of
  exceeding the epidemic threshold from the quantiles, evaluation against the hub's baseline model
  (WIS, coverage).
- **Growth / decline trend** (PPDB-63) — CDC-style trend category with a back-test.
- **EWS reporting channel** (PPDB-64) — anomaly signals distinguish robust from undecidable ones
  when part of the cases arrived through the new channel.
- **STI and tuberculosis registries** (PPDB-65) — scraper, loader and nine charts for the ÚZIS
  registries RPN and RTBC; these diseases are not in ISIN.
- **Source metadata** (PPDB-67) — `source_metadata.py`, `sources.yaml`, `charts.yaml`: freshness,
  licence and periodicity are fetched from the publishers (CSVW, NKOD, HTTP headers, GitHub)
  instead of being copied by hand. Every chart carries a `meta` block with metric, unit, period,
  source and caveats.
- **Documentation** (PPDB-69) — README rewritten in English, `docs/` added, three flowcharts,
  YAML comments translated.
- **Positivity charts** (PPDB-71) — `flu_positivity_seasons.json` (WHO FluNet: influenza
  positivity by week of the season, seasons since 2021/22) and `flu_positivity_weekly.json`
  (ECDC ERVISS: weekly positivity of influenza, RSV and SARS-CoV-2). Two downloaded files that
  no chart read are now used. Five reporting caveats added to `methodology_changes.yaml`.

### Fixed

- **COVID-19 test positivity** (PPDB-71) — `covid_testing.json` divided all positive cases,
  most of them from antigen tests, by the number of PCR tests only. The result was too high
  throughout and, after PCR testing dropped to tens a week in August 2026, went above 100 %
  (the chart clipped it). Positivity is now computed separately for PCR and antigen tests, each
  with its own denominator, with no percentage below 30 tests and without the unfinished last
  week.

## v0.4.0 — 2026-09-06

Detection v3 (π, FDR, registry, reproducibility); Ebola out of the pipeline.

- **Breaking: the Ebola part was removed** (PPDB-53). `process_ebola.py`, `gdrive_ebola.py` and the
  variable `CONTENT_DIR` are gone. The pipeline no longer writes any Hugo pages; Ebola content and
  charts are delivered to the portal as pull requests by an AI agent. The portal tolerates this
  without a change — its pipeline step copies pages only when there are any.
- **Anomaly detection v3** (PPDB-54) — `--as-of` for reproducible runs on an archived snapshot,
  π by parametric bootstrap, Bayesian FDR, the registry of methodology changes applied to
  baselines, a `provenance` block in the output.
- Registry of methodology changes (`methodology_changes.yaml`) and the declared holdout
  (`docs/HOLDOUT.md`).
- Tests of the GLM core against `statsmodels` and a golden test of the scoring.
- Issue-number detection in the PR notification workflows fixed to match `PROJECT_PREFIX`.

## v0.3.0 — 2026-09-03

Anomaly detection, weekly SZÚ data, COVID-19 from open data.

- **Anomaly detection** (PPDB-44) — an early-warning system on ISIN (Farrington/Noufaily),
  validated by a back-test and a simulation study; its output drives the Signals page. The full
  Noufaily model is the default; symmetric re-weighting; an option to leave the COVID years out of
  the baseline.
- **Weekly SZÚ scraper** (`szu_weekly.py`, PPDB-46) — the virus × week matrix from one PDF plus the
  regional reports. It revived the weekly and regional influenza charts and gave the database
  weekly granularity.
- **COVID-19 demographics from MZČR open data** (PPDB-46) — the datasets `osoby`, `umrti` and
  `ockovani-*` replace the non-reproducible `covid.db`. Totals match the summary tiles to the
  unit, and the share of records with a missing age dropped from 12.9 % to 0.5 %. `sqlite3`
  removed from the image.
- **Curated seasons** `curated/szu/` — three closed seasons without an online source.
- The former `flu_weekly` chart, which always drew a single point, was removed and later replaced
  by the real weekly series.
- Provenance notes on the generated Ebola charts; an Ebola card with its own pictogram.

## v0.2.0 — 2026-09-02

Data layer, denominators, fixes of scrapers and charts.

### Data layer (new)

- **Snapshot archive** — every download is stored as a dated gzip with a manifest and sha256
  de-duplication, so it can be shown later which data a published chart was built from.
- **PostgreSQL as the normalised layer** — tables `observation` and `population`, one schema for
  all sources, `snapshot_date` in the unique key for the reporting triangle.
- **The loader ensures the schema itself** by applying `db/init.sql`, so it also works against a
  database that has already run once (PPDB-37).
- **Fix: data duplicated on repeated runs** — the unique key contains columns that are `NULL` for
  some sources; with PostgreSQL's default behaviour the key never matched. Solved with
  `UNIQUE NULLS NOT DISTINCT` (PPDB-37).
- **`generate_json.py` reads from the database** and falls back to CSV when it is unavailable.

### New sources and extensions

- **ČSÚ population** — a new scraper for population by region, which allows **incidence per
  100,000 inhabitants** instead of bare counts.
- **SZÚ: automatic download of the running season** next to the historical PDFs, and missing
  seasons filled in retrospectively.
- **ISIN: disease groups** (PPDB-28) — instead of a ranking of the ten most frequent diseases,
  diagnoses are sorted into thematic groups.

### Scraper fixes

- Wrong season assignment for SZÚ (the season boundary is week 40) and swapped year and week in
  the sort key.
- Data lost from PDF cells containing several viruses at once — only the first value was taken.
- Rate limiting when downloading from Google Drive — reworked to a manifest of identifiers.
- A tool missing from the Docker image, which made part of the charts silently not generate.

### Charts and pages

- **COVID-19 age cohorts** (PPDB-35): records without a birth year used to merge into one
  meaningless cohort. They are now left out of the chart, but their number and share are stated.
- **Ebola outbreak trajectories** (PPDB-39): the current outbreak is computed from its own time
  series, the X axis is numeric and the Y axis logarithmic.
- The Ebola value table is generated statically; the summary tiles are computed from the same
  series as the charts (PPDB-33).

### Environment

- Workflow that triggers the data pipeline on the dev server (PPDB-26).
- A unified contract of the variables `DATA_DIR` / `OUTPUT_DIR` / `CONTENT_DIR` with the portal.

## v0.1.0 — 2026-08-31

The first version that could be plugged into the portal.

- Pipeline scripts moved here from the portal repository; GitHub Actions for issue, branch and PR
  automation and the test CI.
- `Dockerfile` for the `datascrapper` image (PPDB-9) and a single `requirements.txt` (PPDB-7).
- `db/init.sql` — the PostgreSQL schema (PPDB-10).
- **Breaking at the time:** `DATA_IN` / `DATA_OUT` renamed to `DATA_DIR` / `OUTPUT_DIR` (PPDB-22).
- The Ebola pipeline wired into the container (PPDB-24).
- Scraper fixes: SZÚ automation, Ebola de-duplication, `run_all.py` exit code.

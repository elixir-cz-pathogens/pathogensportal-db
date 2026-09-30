# Development

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env        # optional; the defaults work
```

Python 3.12 is what the image and CI use. PostgreSQL is optional; when you want it, version 15 or
newer is required (see [data-model.md](data-model.md)).

`data/` and every `*.csv` are git-ignored, except `curated/` and `tests/fixtures/`.

## Running parts of the pipeline

Every script can be run on its own, and every scraper too:

```bash
python scripts/scrapers/uzis_isin.py          # one source
python scripts/run_all.py                     # all sources + snapshot + source metadata
python scripts/source_metadata.py uzis-isin   # metadata of one source
python scripts/load_to_db.py --dry-run        # count rows without a database
python scripts/generate_json.py               # all charts
python scripts/detect_anomalies.py
python scripts/compute_mem.py
```

A full `run_all.py` downloads several hundred megabytes (the MZČR person-level file alone is about
330 MB) and takes a few minutes.

## Tests

```bash
pytest -q
```

The suite uses no network and no database; HTTP responses are stubbed. CI
(`.github/workflows/python-tests.yml`) runs it on every push and pull request.

| Test file | Guards |
|---|---|
| `test_who_erviss_scrapers.py` | WHO and ERVISS scrapers: changed columns, empty country filter, a source that stopped updating |
| `test_respicast_scraper.py` | RespiCast scraper: country filter, the duplicated median, the round cache |
| `test_uzis_registries.py` | STI and tuberculosis chart generators on small samples |
| `test_source_metadata.py` | metadata discovery, failure isolation, and that every generated chart is in `charts.yaml` |
| `test_glm.py` | the quasi-Poisson GLM against `statsmodels` (skipped when it is not installed) |
| `test_scoring_golden.py` | golden values of the anomaly scoring |
| `test_reporting_channel.py` | the EWS channel verdict |
| `test_mem_golden.py` | `mem.py` against the R package `mem` |
| `test_compute_mem.py` | the week × season matrix |
| `test_trend.py`, `test_forecast.py` | trend categories, exceedance probability |

Golden tests exist so that the method cannot change by accident. If you change the method on
purpose, regenerate the golden values deliberately and say why in the commit.

## Adding a data source

1. **Scraper.** Create `scripts/scrapers/<name>.py` with `download(output_dir: Path) -> list[str]`.
   It should:
   - write CSV files into `output_dir` and return their paths;
   - raise an error on an empty result, on missing expected columns, and — for a live source — on
     a newest record that is too old. A loud failure is better than an empty chart;
   - be safe to run repeatedly.
2. **Register it** in the `jobs` list of `scripts/run_all.py`, with a label and a target directory
   under `$DATA_DIR`. The snapshot then happens automatically.
3. **Describe it** in `sources.yaml`: `id`, publisher, landing page, licence as the source states
   it, the files it produces and at least one `discovery` route. Use the same `id` in the portal's
   catalogue.
4. **Load it** (optional): add a `load_<name>()` function to `scripts/load_to_db.py`, call it in
   both branches of `main()`, and reuse `_upsert_observations()`. Follow the conventions of the
   `observation` table — `NULL` means "not broken down by this dimension".
5. **Test it** without the network: stub the HTTP response and check the failure cases.
   `tests/test_who_erviss_scrapers.py` is a good template.
6. **Document it** in [data-sources.md](data-sources.md) and add it to the data-flow diagram.

## Adding a chart

1. Write a function in `scripts/generate_json.py` that reads the CSV, aggregates, and calls
   `save("<chart_name>", {...})`. Return early with a message when the input file is missing.
2. Call the function in the `__main__` block at the bottom of the file.
3. Add a line for `<chart_name>` to `charts.yaml` (source, metric, unit, grain, region). Without it
   `tests/test_source_metadata.py` fails.
4. Add the file to the catalogue in [chart-generation.md](chart-generation.md).
5. The portal refers to the file by name. A new chart is safe; renaming or removing one is a
   breaking change.

## Recording a reporting change

When a source changes how it reports and the change would look like an epidemic, add a record to
`methodology_changes.yaml` (format in
[analytics/anomaly-detection.md](analytics/anomaly-detection.md#registry-of-methodology-changes))
and list its `id` under `methodology` of the affected source in `sources.yaml`.

## Branches, issues and pull requests

The GitHub automations are described in [../.github/WORKFLOWS_GUIDE.md](../.github/WORKFLOWS_GUIDE.md).
In short:

- Issues are renamed automatically to `PPDB-<number>: …`.
- Name a branch `feature/PPDB-<number>_<slug>`, `bugfix/PPDB-<number>_<slug>` or
  `docs/PPDB-<number>_<slug>` and the issue gets a comment when the branch is pushed.
- Put `PPDB-<number>` into the pull request title and the issue gets a comment when the PR is
  opened and when it is merged.
- A push to `dev` refreshes the dev server — see [deployment.md](deployment.md).

## Language

Documentation and YAML comments are in English. Comments and messages in the Python code, column
names in the CSV files, series labels in the generated JSON and the free-text values in
`methodology_changes.yaml` are Czech; the last three are part of the pipeline's output.

## Diagrams

The flowcharts are draw.io files in [diagrams/](diagrams/) with PNG exports next to them. When a
source, a script or an output changes, update the `.drawio` file and re-export the PNG — the
command is in [diagrams/README.md](diagrams/README.md).

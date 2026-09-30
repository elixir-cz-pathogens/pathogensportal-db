# Architecture

`pathogensportal-db` turns open epidemiological data into the JSON files that the dashboards of
[Pathogen Portal CZ](https://pathogens.vm.cesnet.cz/) display. It is a batch pipeline: it runs,
writes files, and exits. Nothing in it runs continuously.

## The whole picture

![Overview](diagrams/overview.png)

Two flows run side by side and meet in the generators:

- the **data flow** carries the numbers — publisher → scraper → CSV → chart JSON;
- the **metadata flow** carries what the numbers mean — which unit, which source, how fresh, which
  known reporting changes apply — and ends as the `meta` block inside the same JSON file.

## Phases

| # | Phase | Script | Reads | Writes |
|---|---|---|---|---|
| 1 | Download | `run_all.py` + `scrapers/*.py` | the publishers | `$DATA_DIR/<source>/*.csv` |
| 2 | Archive | `snapshot.py` | the files just downloaded | `$DATA_DIR/raw/<date>/…csv.gz`, `raw/manifest.json` |
| 2b | Source metadata | `source_metadata.py` | `sources.yaml`, the publishers, `raw/manifest.json`, `methodology_changes.yaml` | `$DATA_DIR/meta/source_metadata.json` |
| 3 | Normalise | `load_to_db.py` | CSV files | PostgreSQL `observation`, `population` |
| 4 | Charts | `generate_json.py` | CSV files (and PostgreSQL for one chart) | `$OUTPUT_DIR/*.json` |
| 5 | Analytics | `detect_anomalies.py` | ISIN CSV, `methodology_changes.yaml` | `$OUTPUT_DIR/anomaly_signals.json` |
|   |           | `compute_mem.py` | FluID, ERVISS and RespiCast CSV | `$OUTPUT_DIR/flu_mem.json` |

`run_all.py` covers phases 1, 2 and 2b in one process. The container then runs phases 4 and 5:

```
python scripts/run_all.py && python scripts/generate_json.py \
  && python scripts/detect_anomalies.py && python scripts/compute_mem.py
```

Phase 3 is not in that command. `load_to_db.py` is run separately when a database is available.

## Data flow in detail

![Data flow](diagrams/data-flow.png)

How to read it, left to right:

1. **Sources.** Ten publishers, plus three closed SZÚ seasons kept in `curated/szu/` because their
   online source no longer exists.
2. **Scrapers.** One module per source. Each returns the list of files it wrote. What each one
   checks before it accepts a download is described in [data-sources.md](data-sources.md).
3. **CSV files.** The hand-over point. Files marked ◆ are also loaded into PostgreSQL. Two files
   are downloaded and archived but not read by any generator today: `ecdc_covid_cz.csv` and
   `covid_ockovani.csv`.
4. **Generators.** `generate_json.py` has one function per chart. `detect_anomalies.py` and
   `compute_mem.py` each write one larger file. See [chart-generation.md](chart-generation.md),
   [analytics/anomaly-detection.md](analytics/anomaly-detection.md) and
   [analytics/flu-mem.md](analytics/flu-mem.md).
5. **JSON files.** One file per chart, named after the chart.
6. **Portal.** `$OUTPUT_DIR` is mounted onto the portal's `frontend/static/data/charts/`. The
   portal's backend can additionally serve the same payloads from `/api/charts`.

## Metadata flow in detail

![Metadata flow](diagrams/metadata-flow.png)

Three hand-maintained registries drive it:

| Registry | Answers |
|---|---|
| `sources.yaml` | Where can the metadata of this source be found? Which files does the source produce? |
| `charts.yaml` | What does this chart measure — source, metric, unit, grain, region? |
| `methodology_changes.yaml` | Which changes in reporting would otherwise be mistaken for epidemics? |

`source_metadata.py` asks each publisher and writes one JSON file. `chart_meta.py` combines that
file with `charts.yaml` and attaches the result to every chart. The full description is in
[metadata.md](metadata.md).

## Design principles

**Phases hand over through files, not shared state.** A phase can be re-run alone, and the output
of one phase can be inspected with ordinary tools before the next one reads it.

**Every phase is idempotent.** Scrapers overwrite their output. The snapshot skips unchanged files.
The loader upserts on a unique key. Running the pipeline twice gives the same result as running it
once.

**One failing source does not stop the rest.** `run_all.py` wraps each scraper in `try/except`,
continues, snapshots whatever was downloaded, and exits with code 1 if anything failed — so a cron
job or CI notices. Generators skip a chart whose input file is missing instead of failing.

**A scraper fails loudly when the source changes shape.** An empty result, a missing column, or a
newest record that is too old raises an error. A silently empty chart is worse than a red run.

**Nothing is overwritten without a copy.** Publishers quietly revise their historical numbers.
Every changed download is archived with its date, so a published chart can later be traced to the
exact input. See [snapshots.md](snapshots.md).

**The database is optional.** `generate_json.py` tries PostgreSQL for the one chart that joins two
sources and falls back to the CSV files when the database is not reachable. The pipeline works on
a machine with no PostgreSQL at all.

**Metadata must never cost data.** A failed metadata lookup is written into an `errors` field and
the run continues. A chart without a `charts.yaml` entry is still generated, only without `meta`.

**One source of truth per fact.** The schema lives only in `db/init.sql` (the loader applies that
file instead of repeating it in Python). Caveat texts live only in `methodology_changes.yaml`.
Dependencies live only in `requirements.txt`.

## Where the pipeline ends

The pipeline writes JSON and nothing else. It does not write Hugo pages, it does not commit, and it
does not deploy. Committing the refreshed files and publishing them is the portal's side — see
[deployment.md](deployment.md).

## Known loose ends

Recorded here so that they are not mistaken for design decisions:

- `load_to_db.py` is not part of the container command, so the database is filled only when
  someone runs the loader.
- `scripts/placeholder.py` predates the pipeline and is not used.
- `requirements.txt` still lists `gdown`, `beautifulsoup4` and `markdownify`; they were needed by
  the Ebola part removed in v0.4.0.
- The module docstring of `generate_json.py` still names only the MZČR and ECDC inputs.

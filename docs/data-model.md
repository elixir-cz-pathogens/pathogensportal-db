# Data model — the PostgreSQL layer

The schema is in `db/init.sql`. It is the only place the schema is written down: the portal mounts
the file into its database container, and `load_to_db.py` applies the same file before it writes.

The database is the **analytical layer**. Drawing a chart does not need it — `generate_json.py`
reads the CSV files. It is needed for questions across sources: incidence (cases from one source,
population from another), baselines, and the reporting triangle.

## Tables

| Table | Written by | Purpose |
|---|---|---|
| `observation` | `load_to_db.py` | every measurement from every source, in one shape |
| `population` | `load_to_db.py` | denominators from ČSÚ |
| `dashboard_data` | the portal's backend | finished chart payloads served by `/api/charts` |
| `pathogens` | `init.sql` (seed rows) | small reference list of pathogens |

### `observation`

All sources measure essentially the same thing — *how many cases of something there were in some
period, in some area, in some group*. So there is one table for all of them, not one table per
source, and a query across sources needs no `UNION`.

| Column | Type | Meaning |
|---|---|---|
| `source_id` | varchar | which loader wrote the row, see below |
| `diagnosis_code` | varchar, nullable | ICD-10 code where it makes sense |
| `diagnosis_name` | varchar, nullable | diagnosis, virus or indicator name |
| `region_code` | varchar, nullable | NUTS3 code; `NULL` = the whole country |
| `age_group` | varchar, nullable | `NULL` = all ages |
| `sex` | varchar, nullable | `NULL` = both sexes |
| `period_start`, `period_end` | date | the period; `period_end` is exclusive |
| `metric` | varchar | `cases`, `deaths`, `tests`, `hospitalizations`, `lab_detections`, `population_covered`, `rate_per_100k` |
| `value` | numeric | the measurement |
| `snapshot_date` | date | the day this version of the number was loaded |
| `ingested_at` | timestamptz | when the row was written |

Unique key: `(source_id, diagnosis_name, region_code, age_group, sex, period_start, metric,
snapshot_date)`.

Two things about this key need explaining.

**`snapshot_date` is part of the key.** The pipeline does not overwrite a number; it adds a new
version of the same observation. Loading again on the same day rewrites the same rows. Loading on
another day adds a new version. The result is the reporting triangle — the record of how the
numbers for a given period were filled in by late reports — which is what nowcasting needs.

**The key is `UNIQUE NULLS NOT DISTINCT`.** `NULL` here means "not broken down by this dimension"
(the ISIN loader never sets `sex`). By default PostgreSQL treats two `NULL`s as different, so the
key would never match, `ON CONFLICT` would never fire, and every run of the loader would duplicate
the data. This requires PostgreSQL 15 or newer; the portal pins `postgres:16`.

To read the current state, filter on the newest snapshot of a source:

```sql
SELECT region_code, SUM(value) AS cases
FROM observation
WHERE source_id = 'isin'
  AND metric = 'cases'
  AND snapshot_date = (SELECT MAX(snapshot_date) FROM observation WHERE source_id = 'isin')
GROUP BY region_code;
```

### `population`

| Column | Meaning |
|---|---|
| `region_code` | NUTS3 code; `CZ` = the whole country |
| `region_name` | region name |
| `age_group` | `total` (the ČSÚ dataset used is not broken down by age) |
| `sex` | `total`, `male`, `female` |
| `year` | year |
| `value` | number of inhabitants on 31 December |

Primary key: `(region_code, age_group, sex, year)`. A reload overwrites the value.

## What each loader writes

`load_to_db.py` has one function per source. A missing CSV is skipped with a message.

| Loader | Input | `source_id` | `metric` | Grain and notes |
|---|---|---|---|---|
| `load_population` | `csu/population.csv` | — (table `population`) | — | year × region × sex |
| `load_isin` | `isin/isin_infekcni_nemoci.csv` | `isin` | `cases` | month × region × age group × diagnosis; aggregated over sex |
| `load_szu_weekly` | `szu/szu_weekly_viry.csv` | `szu` | `lab_detections` | week × virus, whole country |
|  | `szu/szu_weekly_kraje.csv` | `szu` | `lab_detections`, `tests` | week × region, as the series "Respirační viry (souhrn)" |
| `load_uzis_registries` | `uzis/uzis_pohlavni_nemoci.csv` | `uzis_rpn` | `cases` | year × region × age × sex × diagnosis |
|  | `uzis/uzis_tuberkuloza.csv` | `uzis_rtbc` | `cases` | year × region × age |
| `load_who_flu` | `who/who_flunet_cz.csv` | `who_flunet_sentinel`, `who_flunet_nonsentinel`, `who_flunet` | `lab_detections`, `tests` | week; Influenza, Influenza A, Influenza B, RSV |
|  | `who/who_fluid_cz.csv` | `who_fluid` | `cases`, `population_covered` | week × age group; ILI and ARI |
| `load_erviss` | `ecdc/erviss_ili_ari_cz.csv` | `ecdc_erviss` | `rate_per_100k` | week × age group; ILI and ARI |

Details that are easy to get wrong:

- **Weekly data is the finest grain in the database.** Weeks are ISO weeks; `period_start` is the
  Monday.
- **Prague and Central Bohemia** share laboratories in the SZÚ regional reports and carry the
  combined code `CZ010+CZ020`.
- **FluNet sentinel and non-sentinel** are two different systems reporting for the same week. The
  unique key has no dimension for that, and adding them would mix two populations, so each gets
  its own `source_id`.
- **The FluNet `tests` rows** count specimens tested for influenza; they are stored under the
  series "Influenza" and do not apply to RSV.
- **The ERVISS laboratory file** is not loaded. It contains the same reports as FluNet
  non-sentinel and would enter the database twice.
- **Country of birth** in the tuberculosis registry has no dimension in `observation`. It stays in
  the CSV and in the charts only.
- **Age "All" / "total"** in FluID and ERVISS is stored as `NULL`, following the table's
  convention.
- **MZČR COVID-19 and the SZÚ season summaries** are not loaded; their charts are built from the
  CSV files.

Note that `source_id` values here use underscores (`uzis_rpn`), while the source ids in
`sources.yaml` and `charts.yaml` use hyphens (`uzis-rpn`). They are two separate vocabularies.

## Running the loader

```bash
python scripts/load_to_db.py                              # everything, snapshot_date = today
python scripts/load_to_db.py --dry-run                    # count rows, write nothing, no database needed
python scripts/load_to_db.py --snapshot-date 2026-01-31   # load under a specific snapshot date
```

The connection is configured with `DB_HOST`, `DB_PORT`, `POSTGRES_DB`, `POSTGRES_USER` and
`POSTGRES_PASSWORD`.

### Why the loader applies the schema itself

PostgreSQL runs the scripts in `docker-entrypoint-initdb.d` only when it initialises an empty data
volume. On a database that has already run once, `init.sql` is not executed again, so tables added
later would never be created and the loader would fail with "relation does not exist".
`load_to_db.py` therefore executes `db/init.sql` on every run. The file is fully idempotent
(`CREATE TABLE IF NOT EXISTS`, `ON CONFLICT DO NOTHING`), so this is safe.

## How the generators use the database

Only one chart reads PostgreSQL today: `isin_regional_incidence`, which joins ISIN cases with the
ČSÚ population. `generate_json.py` tries the SQL query first and falls back to the CSV files when
the database is unreachable or empty. Both paths produce the same JSON.

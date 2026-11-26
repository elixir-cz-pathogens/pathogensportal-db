# Pathogen registry — the information layer

The registry answers questions *about* the pathogens the portal covers, not about their case
counts: what a pathogen is called in Czech and in English, how the authorities identify it, under
which ICD-10 codes its disease appears in the data, on which dashboards a reader finds it, and when
its data last moved.

| | |
|---|---|
| Source of truth | [`curated/pathogens.yaml`](../curated/pathogens.yaml) — edited by hand, reviewed in pull requests |
| Database copy | PostgreSQL tables `pathogens`, `pathogen_*`, `diagnosis`, `dashboard`, `identifier_scheme` ([`db/init.sql`](../db/init.sql)) |
| Loader | `scripts/load_pathogens.py` (also called by `load_to_db.py`) |
| Check against the authorities | `scripts/verify_pathogens.py` (needs the network; not part of the pipeline or CI) |

The database is a copy that can be rebuilt at any time. Never edit it by hand — the next run of
the loader overwrites the change. Edit the YAML.

## Why a file and a database, not just one of them

A hand-kept list needs review, history and a diff; that is what a file in git gives. A question
such as "which dashboards show a pathogen of the family *Flaviviridae* and how old is their data"
needs joins; that is what the database gives. So the file is where the registry is *maintained*
and the database is where it is *asked*. It is the same split as everywhere else in this
repository: curated YAML in, derived data out.

It also keeps the portal's rule that the site works with no backend. Nothing on the site depends
on these tables; they serve the analytical layer and the planned MCP/AI layer.

## Where each value comes from

| Field | Authority | How it is checked |
|---|---|---|
| `taxon`, `scientific_name`, `rank` | NCBI Taxonomy, as served by ENA (EMBL-EBI). ENA, NCBI and UniProt share the number. | `verify_pathogens.py` compares the name and the rank with `https://www.ebi.ac.uk/ena/taxonomy/rest/tax-id/<taxon>` |
| `species` | ICTV binomial species name, for viruses whose commonly used node sits below the species (*Influenza A virus* → *Alphainfluenzavirus influenzae*) | must be in the ENA lineage of the taxon |
| `family` | taxonomic family | must be in the ENA lineage of the taxon |
| `ids.wikidata` | the Wikidata item whose "NCBI taxonomy ID" (P685) equals `taxon` | queried from Wikidata |
| `ids.mesh` | the MeSH descriptor that Wikidata item states (P486) | the descriptor must exist at `id.nlm.nih.gov` |
| `icd10`, `icd10_shared` | WHO ICD-10 Version 2019 (the Czech MKN-10 uses the same codes) | every code must be in the code list (see below); the first fill was checked against the WHO ICD-10 browser on 2026-11-26 |
| official disease titles | Czech: MKN-10 as published by ÚZIS; English: WHO ICD-10 2019 | not stored in the registry — looked up by code in `curated/labels_en.yaml` |
| `name`, `disease`, `synonyms`, `abbreviations` | the portal's own reader-facing wording | editorial; for the pathogens of the genomic table the disease name is the one that table shows |
| `dashboards` | the portal's pages | every path must be listed in the `dashboards` section of the file |
| `verified` | the day somebody last checked the record against the sources above | a date, never in the future |

Two rules follow from the project's habit of preferring a gap to an invented value:

- **A pathogen with no matching Wikidata item has no `ids`.** Three records are in that state
  (the *M. tuberculosis* complex, Andes virus, Bundibugyo virus).
- **A Czech MKN-10 title that is not in the repository stays empty.** `curated/labels_en.yaml`
  holds the official titles of the codes present in the ISIN data. The registry refers to 30 more
  codes (tuberculosis A15–A19, HIV B20–B24, COVID-19 U07.1, four-character codes such as A04.5);
  for those it carries the English WHO title only. `load_pathogens.py --dry-run` lists them.

### Exclusive and shared codes

`icd10` holds codes that mean this pathogen's disease and nothing else (B05 measles). `icd10_shared`
holds codes whose heading covers the pathogen *and others*: A04 "other bacterial intestinal
infections" is Campylobacter and more, J10 is influenza A and B together.

Counts under a shared code must never be attributed to one pathogen. A query that sums cases per
pathogen filters `NOT shared` — see the example below. The check refuses a code that two unrelated
pathogens both claim as exclusive.

## Tables

| Table | One row per | Notes |
|---|---|---|
| `pathogens` | pathogen | names (`name_cs`, `name_en`), disease (`disease_cs`, `disease_en`), `scientific_name`, `species`, `family`, `category`, `taxon_id`, `taxon_rank`, `taxon_note`, `parent_slug`, `verified_on`, `updated_at`. The table existed before the registry with four seed rows; it is extended with `ADD COLUMN IF NOT EXISTS`, and the old column `name` keeps the English name. |
| `pathogen_identifier` | pathogen × scheme × value | `ncbi-taxon` and `ena-taxon` (both filled from `taxon`), `wikidata`, `mesh` |
| `identifier_scheme` | scheme | title, authority, home page and the URI pattern of one record |
| `pathogen_term` | pathogen × language × kind × term | the glossary: `scientific`, `species` (Latin), `pathogen`, `disease`, `synonym`, `abbreviation` (Czech and English), each with its `source` (`ncbi-taxonomy`, `ictv`, `portal`) |
| `diagnosis` | ICD-10 code | official titles `name_cs`, `name_en` and the portal's reader-facing `reader_cs`, `reader_en`; `code` is the same code as `observation.diagnosis_code` |
| `pathogen_diagnosis` | pathogen × code | `shared` = the code covers other pathogens too |
| `dashboard` | portal page | `kind` (`surveillance`, `genomics`, `report`, `external`), `chart`, `data_through`, `data_generated_at` |
| `pathogen_dashboard` | pathogen × page | |

### Two dates on a pathogen, two on a page

| Column | Says |
|---|---|
| `pathogens.verified_on` | when a person last compared the record with the authorities (`verified` in the YAML) |
| `pathogens.updated_at` | when the record last **changed** in this database. The loader stores a hash of the record and leaves `updated_at` alone when the hash is the same, so the column does not turn into "the loader ran today". |
| `dashboard.data_through` | the last day the page's data covers (`meta.period.end_date` of its chart; `posledni_datum` or the end of `obdobi` for charts without a time axis) |
| `dashboard.data_generated_at` | when the pipeline last wrote that chart file (`generated_at`) |

`dashboard.chart` names the chart JSON that carries the page's data date — the same file the page
names in `update_from` in the portal. The loader reads it from `$OUTPUT_DIR` on every run. A page
without its own data (a situational report, an external dashboard) has no chart and no dates, and
when `$OUTPUT_DIR` holds no charts the two columns are simply empty.

## Views

| View | Content |
|---|---|
| `pathogen_overview` | one row per pathogen: names, taxon, `icd10` and `icd10_shared` as arrays, `dashboards` as an array, the newest `data_through` and `data_generated_at` among its pages, `verified_on`, `updated_at` |
| `pathogen_glossary` | every term of `pathogen_term`, plus the official Czech and English titles of the codes that belong to the pathogen exclusively (`kind = 'diagnosis'`, `source` `mkn-10` / `icd-10-who`) |
| `pathogen_ids` | identifiers with the scheme title, the authority and the URI of the record |

```sql
-- Which pathogen is "pneumokok"? (search the glossary in any language)
SELECT DISTINCT slug FROM pathogen_glossary WHERE lower(term) = 'pneumokok';

-- Everything about one pathogen
SELECT * FROM pathogen_overview WHERE slug = 'influenza-a';

-- Its identifiers, with links to ENA (EMBL-EBI), NCBI, Wikidata and MeSH
SELECT scheme, value, uri FROM pathogen_ids WHERE slug = 'influenza-a';

-- Pathogens whose dashboards have not received data for a year
SELECT slug, name_cs, data_through FROM pathogen_overview
 WHERE data_through < CURRENT_DATE - INTERVAL '1 year' ORDER BY data_through;

-- Cases per pathogen from the observation table — exclusive codes only
SELECT p.slug, sum(o.value) AS cases
  FROM observation o
  JOIN pathogen_diagnosis d ON d.code = o.diagnosis_code AND NOT d.shared
  JOIN pathogens p ON p.id = d.pathogen_id
 WHERE o.source_id = 'isin' AND o.metric = 'cases'
   AND o.snapshot_date = (SELECT max(snapshot_date) FROM observation WHERE source_id = 'isin')
 GROUP BY p.slug;
```

## Loading

```bash
python scripts/load_pathogens.py --dry-run   # check the registry and count rows; no database needed
python scripts/load_pathogens.py             # apply db/init.sql, then load
python scripts/load_to_db.py                 # loads the registry together with the observations
```

The connection uses the same variables as `load_to_db.py` (`DB_HOST`, `DB_PORT`, `POSTGRES_*`);
`OUTPUT_DIR` is where the data dates of the pages are read from.

The loader makes the database match the registry exactly: new records are inserted, changed ones
rewritten together with their identifiers, terms, codes and pages, and records that left the
registry are deleted. A repeated run changes nothing. The registry is checked before anything is
written, and all findings are reported at once.

Inside `load_to_db.py` the registry runs in its own savepoint. A mistake in the registry is
reported, the observations are still loaded, and the script exits with code 1.

The container command does not call the loader — like `load_to_db.py`, it needs a database, and
the chart pipeline runs without one.

## Checking against the authorities

```bash
python scripts/verify_pathogens.py                  # the whole registry
python scripts/verify_pathogens.py influenza-a tbe  # selected records
```

The script asks ENA, Wikidata and NLM and lists every difference. Exit code 0 means everything
matches, 1 means at least one difference, 2 means a source did not answer. Virus taxonomy is
revised almost every year, so run it before a release and after an ICTV update; then set
`verified` of the checked records to the day of the check.

## Adding a pathogen

1. Find the taxon in the [ENA browser](https://www.ebi.ac.uk/ena/browser/) or NCBI Taxonomy. Pick
   the node the surveillance data mean — a genus when the data do not distinguish species
   (*Salmonella*, *Campylobacter*). When no node fits exactly, take the nearest one and say why in
   `taxon_note`.
2. Add a record to `curated/pathogens.yaml`. Copy `scientific_name` and `rank` from ENA letter for
   letter. Keep the `id` the pathogen already has in `curated/genomics_taxa.yaml`, if it is there.
3. Write the Czech and English `name` (the pathogen) and `disease`.
4. Add ICD-10 codes: `icd10` when the code is this pathogen's alone, `icd10_shared` otherwise. A
   code that is not in `curated/labels_en.yaml` goes into the `diagnoses` section with its WHO
   title.
5. List the `dashboards`; a page that is not in the `dashboards` section yet goes there too, with
   its `kind` and — when it has its own data — its `chart`.
6. Run `python scripts/verify_pathogens.py <id>` and `pytest -q tests/test_pathogen_registry.py`.

## Relation to `curated/genomics_taxa.yaml`

`genomics_taxa.yaml` lists the pathogens of the genomic table and is read by
`compute_genomics.py`. The registry repeats their `id`, `taxon` and page. Until the genomic
generator reads the registry directly, the check (`pathogen_registry.validate`) and the tests fail
when the two files disagree on any of the three.

## Limits

- The registry covers the pathogens that have a place on the portal (48 at the first fill), not
  every notifiable disease. Every diagnosis of the ISIN data is in `diagnosis`, but only those with a
  clear single cause are linked to a pathogen.
- Czech pathogen names are the portal's wording. No Czech standard names pathogens the way ICTV
  and NCBI Taxonomy do in Latin and English; the official Czech layer is the MKN-10 title of the
  disease.
- Page titles are not copied from the portal — they would go stale. `dashboard.path` is the stable
  key; the English page is the same path under `/en`.
- `dashboard.chart` repeats the portal's `update_from`. Nothing checks the two against each other
  across the repositories.

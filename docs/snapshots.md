# Snapshots — the dated archive

`scripts/snapshot.py` keeps an immutable history of what came from each source and when.
`run_all.py` calls it after the scrapers, with the list of files they returned — also after a
partial failure, because whatever was downloaded is worth keeping.

## Why

Scrapers overwrite their output files in place. Without an archive there is no way to tell what
the data looked like on a given day, and that matters for three reasons:

- **Reproducibility.** A chart published last month can be traced to the exact input it was built
  from.
- **Silent revisions.** Open sources routinely correct their historical numbers without notice
  (SZÚ does, by units to tens of cases per virus and season).
- **The reporting triangle.** The last few weeks of any surveillance series look artificially low,
  because reports keep arriving late. Correcting for that (nowcasting) needs the history of how the
  numbers for a given period filled in over time.

## Layout

```
$DATA_DIR/raw/
├── manifest.json
├── 2026-09-01/
│   ├── isin/isin_infekcni_nemoci.csv.gz
│   └── mzcr/covid_pripady.csv.gz
└── 2026-09-08/
    └── mzcr/covid_pripady.csv.gz        ← only files that changed since the last snapshot
```

A snapshot of a file goes to `raw/<YYYY-MM-DD>/<path relative to DATA_DIR>.gz`.

`manifest.json` holds, for every file, the sha256 of its last archived content and the date of
that snapshot:

```json
{
  "isin/isin_infekcni_nemoci.csv": { "sha256": "9f2c…", "snapshot": "2026-09-01" }
}
```

## De-duplication

Before archiving, the file's sha256 is compared with the manifest. Unchanged content is not stored
again. Without this, about 65 MB of CSV per run would add up to roughly 24 GB a year of mostly
identical copies.

A consequence worth knowing: a day's directory contains only the files that changed that day. To
find the version of a file that was valid on a given date, take the newest directory that is not
later than that date and contains the file. `detect_anomalies.py --as-of` does exactly this.

## Who uses the archive

| Consumer | How |
|---|---|
| `detect_anomalies.py --as-of YYYY-MM-DD` | runs on the archived ISIN file instead of the live CSV; the output is bit-identical on every re-run and carries the input's sha256 in its `provenance` block |
| `source_metadata.py` | reads `snapshot` dates from the manifest to report when we last fetched each source |
| `load_to_db.py --snapshot-date` | loads a CSV into the database under a chosen snapshot date |

## What is not archived

- The raw MZČR `osoby.csv` (about 330 MB). Only its aggregate is stored and archived.
- Downloaded PDFs. The SZÚ regional PDFs and the RespiCast round files are cached next to the CSVs
  (`szu/pdf_kraje/`, `ecdc/respicast_cache/`) but are not part of `raw/`.

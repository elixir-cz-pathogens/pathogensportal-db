# Data sources and scrapers

Every source has one scraper module in `scripts/scrapers/`. All of them have the same shape:

```python
def download(output_dir: Path) -> list[str]:
    """Download, validate, write CSV files into output_dir, return their paths."""
```

`scripts/run_all.py` calls them one after another, each inside `try/except`. The returned paths
are handed to `snapshot.py`, so everything a scraper reports is archived. A scraper can also be run
alone, for example `python scripts/scrapers/uzis_isin.py`.

The diagram shows which scraper produces which file and who reads it:

![Data flow](diagrams/data-flow.png)

## Summary

| Source | Scraper | Output in `$DATA_DIR` | Grain | In PostgreSQL | Read by |
|---|---|---|---|---|---|
| MZČR COVID-19 | `mzcr_covid.py` | `mzcr/covid_*.csv` (9 files) | day; month × age × region | no | `generate_json.py` |
| ÚZIS ISIN | `uzis_isin.py` | `isin/isin_infekcni_nemoci.csv` | month × region × age × diagnosis | yes | `generate_json.py`, `detect_anomalies.py` |
| ÚZIS ISIN weekly summary | `uzis_isin_weekly.py` | `isin/isin_tydenni.csv`, `isin/isin_tydenni_stav.json` | ISO week × region × age × diagnosis (11 diagnoses) | no | `compute_isin.py` |
| ÚZIS daily e-surveillance | `uzis_esurveillance.py` | `uzis_esurv/esurv_*.csv` (6 files), `uzis_esurv/esurv_stav.json` | day × region × age group; influenza, COVID-19, RSV, ARI/ILI | national by age, regions in total | `compute_esurv.py` |
| ÚZIS RPN + RTBC | `uzis_registries.py` | `uzis/uzis_pohlavni_nemoci.csv`, `uzis/uzis_tuberkuloza.csv` | year (TB: quarter) | yes | `generate_json.py` |
| SZÚ seasonal | `szu_influenza.py` | `szu/szu_influenza_<season>.csv` | season total per virus | no | `generate_json.py` |
| SZÚ weekly | `szu_weekly.py` | `szu/szu_weekly_viry.csv`, `szu/szu_weekly_kraje.csv` | week | yes | `generate_json.py` |
| ČSÚ | `csu_population.py` | `csu/population.csv`, `csu/population_age.csv` | year × region × sex; year × single year of age | regions only | `generate_json.py`, `compute_isin.py` |
| WHO FluNet + FluID | `who_flu.py` | `who/who_flunet_cz.csv`, `who/who_fluid_cz.csv` | week | yes | `generate_json.py` (FluNet), `compute_mem.py` (FluID) |
| ECDC ERVISS | `ecdc_erviss.py` | `ecdc/erviss_ili_ari_cz.csv`, `ecdc/erviss_nonsentinel_cz.csv` | week | rates only | `generate_json.py` (lab file), `compute_mem.py` (rates) |
| ECDC ERVISS snapshots | `ecdc_erviss_snapshots.py` | `ecdc/erviss_snapshots/<date>_nonSentinelTestsDetections.csv.gz` | week × snapshot | no (immutable already) | `compute_nowcast.py` |
| ECDC RespiCast | `ecdc_respicast.py` | `ecdc/respicast_cz.csv` | weekly forecast round | no | `compute_mem.py` |
| ECDC COVID-19 | `ecdc_covid.py` | `ecdc/ecdc_covid_cz.csv` | day | no | nothing — archived only |

Column names in the CSV files are Czech (`rok`, `tyden`, `kraj_kod`, `pocet_pripadu`, …), because
most of them come from the publishers as they are.

## Czech surveillance

### MZČR — COVID-19 open data

- **Where:** `https://onemocneni-aktualne.mzcr.cz/api/v2/covid-19/<dataset>.csv`, updated daily.
- **Scraper:** `mzcr_covid.py`
- **What it does:** downloads eight datasets and stores them under our own names; the date column
  is normalised to a plain date. A ninth dataset, `osoby.csv` (one row per case, about 330 MB and
  5 million rows), is streamed to a temporary file and aggregated in chunks to
  year × month × age × region. The raw file is neither kept nor archived.
- **Output:**

  | File | From |
  |---|---|
  | `covid_pripady.csv` | `nakazeni-vyleceni-umrti-testy.csv` |
  | `covid_hospitalizace.csv` | `hospitalizace.csv` |
  | `covid_testy.csv` | `testy-pcr-antigenni.csv` |
  | `covid_ockovani.csv` | `ockovani.csv` |
  | `covid_incidence.csv` | `incidence-7-14-cr.csv` |
  | `covid_umrti.csv` | `umrti.csv` — deaths with age, since March 2020 |
  | `covid_vax_pozitivni.csv` | `ockovani-pozitivni.csv` — cases by vaccination status, since January 2021 |
  | `covid_vax_hospitalizace.csv` | `ockovani-hospitalizace.csv` |
  | `covid_osoby_agg.csv` | `osoby.csv`, aggregated: `rok, mesic, vek, kraj_nuts_kod, pripady` |

- **Checks:** an empty dataset raises an error; the `osoby` aggregate must contain at least one
  million cases.
- **Good to know:**
  - A missing age is kept as `vek = -1` and a missing region as `CZ999`, so the share of
    incomplete records (about 0.5 %) can be stated on the portal instead of being dropped
    silently.
  - `covid_testy.csv`: from the week of 17 August 2026 PCR tests dropped from 300–650 to 70–190 a
    week, while thousands of antigen tests are still reported. Most positive cases now come from
    antigen tests, so `incidence_pozitivni` must not be divided by PCR tests (registry entry
    `mzcr-pcr-testy-2026-08`).

### ÚZIS — ISIN (notifiable infectious diseases)

- **Where:** one CSV at `datanzis.uzis.gov.cz` (dataset NR-27-01).
- **Scraper:** `uzis_isin.py` — a plain file download with no transformation.
- **Output:** `isin/isin_infekcni_nemoci.csv` with the columns `rok`, `mesic`, `kraj_kod`,
  `kraj_nazev`, `vek_kod`, `vek_nazev`, `diagnoza`, `diagnoza_nazev`, `pocet_pripadu`, `EWS`.
- **Content:** 114 diagnoses by region, month and age group. This is the main source of the
  dashboards and the only input of the anomaly detection.
- **Good to know:**
  - The open export currently ends in December 2025. Silence in later months means missing data,
    not calm (registry entry `isin-horizont-2025-12`).
  - Since July 2025 ÚZIS also receives cases through a new reporting channel (EWS). The `EWS`
    column says how many cases of a row arrived that way. This raises several series without more
    people being ill — see [analytics/anomaly-detection.md](analytics/anomaly-detection.md).
  - Region `CZ999` means "not stated".
  - Syphilis, gonorrhoea and tuberculosis are **not** in ISIN; they have their own registries.

### ÚZIS — weekly summary of selected infectious diseases

- **Where:** the data summary NR-27-04 "Meziroční srovnání počtu infekčních nemocí po týdnech"
  ([NZIP page](https://www.nzip.cz/data/2620-infekcni-nemoci-mezirocni-srovnani-datovy-souhrn)).
  The files sit on a shared storage of ÚZIS; the link `…/download` returns everything as one ZIP
  (about 32 MB) without a login.
- **Scraper:** `uzis_isin_weekly.py`
- **Why next to the open ISIN file:** the open CSV has every diagnosis but is updated once a year
  and ends with last December. The summary has 11 diagnoses, by ISO week up to the previous
  week, and — the reason it is needed — a separate count of records based on EWS reports.
- **Shape of the source:** folders `Infekce/<Monday>/`; only the last four or five hold files, the
  future Mondays are created ahead and empty. One XLSX per diagnosis, 15 sheets (the country and
  14 regions; the region name is read from cell A9, because one sheet name is wrong in the
  source). On a sheet the years lie side by side as column blocks, each with pairs of columns
  ISIN / EWS per age group. "ISIN" is every record, "EWS" the records among them that were
  created from an EWS report — not additional cases.
- **Output:**
  - `isin/isin_tydenni.csv` — `diagnoza` (ICD-10), `kraj_kod` (`CZ` or NUTS3), `rok`, `tyden` (ISO),
    `vek` (age group or `Celkem`), `pocet`, `pocet_ews`
  - `isin/isin_tydenni_stav.json` — the folder taken, the status date the source states in the
    file header, and the diagnosis names as the source writes them
- **Checks:** every year in the header row must have its block (a block that lost its header
  would drop the current year silently); weeks must run from 1 without a gap; the age groups
  must add up to `Celkem`; EWS must not exceed the total; all 15 areas must be present. A newest
  folder older than three weeks fails the download.
- **Good to know:**
  - It is a side source in `run_all.py`: a failure does not stop the refresh of the other data,
    and the page keeps the last state.
  - The archive is downloaded once a week — while the folder of the current week is on disk the
    scraper returns the files it has.
  - Weekly counts were stable across four releases checked in October and November 2026: a week
    did not change by more than two cases after it was first published.
  - In the running year the regional sheets add up to less than the national one; the portal
    draws only the national series. See `isin-tydenni-kraje-bez-kraje` in
    `methodology_changes.yaml`.
  - The source states no licence, only a citation. See `sources.yaml`.

### ÚZIS — daily e-surveillance of respiratory infections

- **Where:** the data summary "Respirační infekce – denní reporting e-surveillance"
  ([NZIP page](https://www.nzip.cz/data/2860-respiracni-infekce-denni-reporting-esurveillance-datovy-souhrn)).
  The files sit on the shared storage of ÚZIS (Nextcloud): one folder `YYYY-MM-DD/` per day with
  56 files (about 21 MB), state as of the previous day. The reports are produced within the
  SCOPE IS project (EU4Health).
- **Scraper:** `uzis_esurveillance.py`
- **What it is:** automated reports. New cases are reported by physicians to ISIN (the early
  warning system, EWS); hospitalisations by the connected hospitals, counting every hospital stay
  during which the infection was recorded; laboratory confirmations by the laboratories connected
  to EWS; vaccine doses come from the Central Vaccination Register. Four conditions: influenza,
  COVID-19, RSV, and ARI/ILI in total.
- **Why it matters:** it is the only public source of hospitalisations with influenza and RSV
  in Czechia, and it is one to two weeks fresher than the weekly ILI/ARI rates.
- **Shape of the source:** every file carries the whole history, so only the newest folder is
  read, and only 24 of its files (about 11 MB). Dates are text (`24.11.2026`). The daily file
  (`EW05`) has five column blocks side by side on a sheet per region; positions are read from
  the header, not from fixed column numbers. The folder listing needs the header
  `X-Requested-With: XMLHttpRequest` (the public WebDAV endpoint answers 401 without it); the
  `/download` link returns the whole storage as one ZIP (2.7 GB) and is not used.
- **Output** (`uzis_esurv/`):
  - `esurv_denni.csv` — `datum`, `nemoc` (`chripka`, `covid19`, `rsv`, `ari_ili`), `kraj_kod` (`CZ`
    or NUTS3), `vek` (`celkem` or one of 12 age groups), `pripady`, `hosp_nove`, `hosp_aktualne`,
    `jip_nove`, `umrti_hosp`; daily since 26 May 2025
  - `esurv_nemocnice.csv` — daily hospital figures with the type of care of new admissions
    (`jip`, `aro`, `kyslik`, `hfno`, `upv_ecmo`) and deaths
  - `esurv_ockovani.csv` — doses given per day by region of residence and by age group
  - `esurv_laboratore.csv` — laboratory-confirmed infections per day and the number of
    reporting laboratories
  - `esurv_uzemi.csv` — regions and districts on the last day: 7-day and 14-day incidence and
    hospital figures per 100,000 as computed by the source
  - `esurv_ockovani_stav.csv` — cases, admissions and intensive-care admissions by vaccination
    in the season (influenza, COVID-19); archived, not charted
  - `esurv_stav.json` — the folder taken, the status date, the processing time at the source,
    the vaccines the source lists
- **Checks:** all 24 files must be present; every sheet must have its header columns and, in
  the daily file, all four age blocks with 12 age groups; the files of one folder must state
  the same status date; identical repeated rows are merged, the same key with different
  numbers fails. A newest folder older than a week fails the download.
- **Not taken:** the per-hospital overview (`eHos02`) and the per-municipality vaccination file
  (`eVak03`) — the portal shows nothing below the district level; `EW02` and `EW06` are
  recomputations of the daily file; the DOCX and PDF summaries repeat the same numbers.
- **Good to know:**
  - It is a side source in `run_all.py`: a failure does not stop the refresh of the other data.
  - The folder listing is asked on every run; the files are downloaded once per folder. Asking
    the listing counts as a check of the source for `last_checked.json`.
  - National figures agree between the daily file and the hospital file on every day. Regional
    figures differ between them (the national sum is the same): the daily file and the region
    table are by the patient's residence. Maps and age groups therefore come from the daily
    file and `EW03`; from the hospital file only the type of care for the whole country.
  - The laboratory file repeats some days with identical numbers (6 of 91 in the folder of
    25 November 2026).
  - The reported cases are **not** sentinel ILI/ARI rates and must not be compared with the
    ERVISS series or the MEM thresholds. See `esurv-hlaseni-lekaru-a-nemocnic` in
    `methodology_changes.yaml`.
  - The source states no licence, only a citation. See `sources.yaml`.

### ÚZIS — registries RPN and RTBC

- **Where:** two CSV files at `datanzis.uzis.gov.cz` (datasets NR-29-01 and NR-30-01), updated once
  a year with roughly a one-year delay.
- **Scraper:** `uzis_registries.py`
- **Output:**
  - `uzis/uzis_pohlavni_nemoci.csv` — sexually transmitted infections since 1994: A50–A53 syphilis,
    A54 gonorrhoea, A55 lymphogranuloma venereum; district of residence × sex × age × diagnosis ×
    year.
  - `uzis/uzis_tuberkuloza.csv` — tuberculosis (A15–A19) since 2000: year and quarter of incidence
    × district × region × age × country of birth (the `CZ` flag).
- **Checks:** the expected columns must be present, the file must not be empty, and the last year
  must not be more than three years old — otherwise nobody would notice that the dataset stopped
  being updated.
- **Good to know:** both files are small and are stored byte for byte as published.

### SZÚ — seasonal influenza archives

- **Where:** ZIP archives of PDFs for the seasons 2012/13–2021/22, and the listing page of the
  running season at `szu.gov.cz`.
- **Scraper:** `szu_influenza.py`
- **What it does:**
  - `download()` — for each historical season, takes the last PDF of the season from the ZIP and
    extracts its table with `pdfplumber`. Three PDF layouts are handled. A season that already has
    a CSV is skipped.
  - `download_current()` — finds the newest "by virus type" PDF on the listing page, works out the
    season from the week number (a season starts in week 40), reads the virus × week matrix with
    `szu_weekly.parse_viry_matrix` and sums it into season totals. It rewrites the CSV of every
    season in that PDF that has no curated file — the running one and the previous one, because
    SZÚ keeps correcting it. These totals carry only "Detekce viru"; serology and isolation are
    not read by anything.
  - Seasons 2022/23–2024/25 are not in the ZIP archive and not on the listing page the scraper
    reads. SZÚ moved them under Témata › A–Z infekce › Chřipka (one page per season, weekly PDFs
    only, e.g. [2024/25](https://szu.gov.cz/temata-zdravi-a-bezpecnosti/a-z-infekce/ch/chripka/zprava-o-chripkove-aktivite-hlaseni-a-vysledky-laboratornich-vysetreni/chripkova-sezona-2024-2025/)).
    Being PDFs, they are deliberately not connected as a source; the CSVs stay in `curated/szu/`
    and are copied into `$DATA_DIR/szu/` when missing.
  - **Can FluNet replace them?** Not today (checked 2026-10-25). FluNet (`VIW_FNT`, CZE,
    `NONSENTINEL`) has all 52 weeks of each of the three seasons, but it carries only influenza
    and RSV — adenovirus, rhinovirus, metapneumovirus, parainfluenza, seasonal coronaviruses and
    bocavirus are empty. For the viruses it does carry, season sums (weeks 40–39; SZÚ
    "Influenza A" = FluNet `ANOTSUBTYPED`) match 2024/25 within ±1 per virus, but differ in
    2022/23 (A/H3N2 −128, Influenza B −100, A not subtyped +107) and 2023/24 (RSV +134,
    A/H1N1pdm −94).
- **Output:** `szu/szu_influenza_<YYYY>_<YYYY>.csv` with `sezona, rok, tyden_kt, kategorie, virus,
  pocet`. From 2013/14 on the rows are season totals (`tyden_kt = 0`).
- **Good to know:** SZÚ corrects historical laboratory counts after the fact, by units to tens of
  cases per virus and season (registry entry `szu-retro-korekce`).

### SZÚ — weekly PDFs

- **Where:** the same listing page; it links a PDF for every week of the running season, in two
  kinds.
- **Scraper:** `szu_weekly.py`
- **What it does:**
  - "By virus type": only the newest PDF is needed — it carries the whole virus × week matrix for
    the running season and the previous one (from week 40/2026 on, the two previous ones: three
    tables side by side). The matrix is read from character positions, because the header labels
    run together. A year that does not fit its header cell is printed as `###`; it is inferred
    from the neighbouring table.
  - "By region": one PDF per week, reported by virological laboratory; laboratories of the same
    region are summed. The PDFs are cached by file name in `szu/pdf_kraje/`. The week is read
    from the table, not from the file name: on 16 November 2026 SZÚ published a corrected
    week 45 under the name of week 46. When two PDFs carry the same week, the later file
    replaces the earlier one; a PDF whose name and table disagree is downloaded again on every
    run, because SZÚ may replace it under the same name.
- **Output:**
  - `szu/szu_weekly_viry.csv` — `sezona, rok, tyden, virus, pocet`
  - `szu/szu_weekly_kraje.csv` — `rok, tyden, kraj, pozitivni, vysetreno`
- **Checks:** in every table that has a "cumulative" column, the weekly values must add up to it
  for every virus; on a mismatch the parser raises an error rather than return wrong numbers.
  Tests run on real PDFs of every layout met so far (`tests/fixtures/szu/`), including the
  two regional PDFs that carry the same week.
- **Good to know:** Prague and Central Bohemia share laboratories and cannot be separated; they
  carry the combined code `CZ010+CZ020`.

### ČSÚ — population

- **Where:** DataStat API, dataset `PORKR01`, one GET request.
- **Scraper:** `csu_population.py`
- **Output:** `csu/population.csv` — `rok, kraj_kod, kraj_nazev, pohlavi, pocet`. `kraj_kod` is the
  NUTS3 code, `CZ` for the whole country.
- **Checks:** every expected region must be present.
- **By age:** dataset `WGEN01AG1` (population by single year of age, whole country) goes to
  `csu/population_age.csv` — `rok, vek, pocet`, both sexes together, `vek` 100 = 100 and over.
  Single years can be summed into any age groups, which the two ISIN sources need because their
  groups differ. A failure of this download is reported and skipped: the population by region
  is a required source and must not fail because of an addition.
- **Good to know:** these are the denominators. Without them the charts show counts, and a map of
  counts is mostly a map of where more people live. The figure is the population on 31 December,
  not the mid-year population; the difference is a few tenths of a percent.

## International context

### WHO — FluNet and FluID

- **Where:** WHO xMart API (`FLUMART`), views `VIW_FNT` (FluNet) and `VIW_FID_EPI` (FluID),
  filtered to `COUNTRY_CODE = 'CZE'`.
- **Scraper:** `who_flu.py`
- **Output:**
  - `who/who_flunet_cz.csv` — weekly laboratory detections since 1997: `rok, tyden, tyden_od,
    zdroj, vysetreno, inf_a, inf_b, inf_celkem, a_h1n1pdm, a_h3, a_nesubtyp, rsv`
  - `who/who_fluid_cz.csv` — weekly ILI and ARI cases with the population covered, continuous
    since 2009/10: `rok, tyden, tyden_od, vek, ili_pripady, ili_populace, ari_pripady,
    ari_populace`
- **Checks:** the filter must return rows, the expected columns must exist, and the newest week
  must not be older than 45 days (FluNet) or 180 days (FluID, which is reported sparsely outside
  the season).
- **Good to know:**
  - The columns are nullable integers, and an empty count is ambiguous. Until 2025 a week
    without influenza had `inf_a = 0`, `inf_b = 0` and an empty `inf_celkem`. Since week 21/2025
    such a week has all detection columns empty while `vysetreno` is filled in. The last two or
    three weeks look the same, but there the results simply have not been reported yet. The
    positivity charts treat an empty field inside the series as zero and at its end as unknown
    (registry entry `lab-nulove-tydny-2025-05`).
  - The number of specimens tested changed by orders of magnitude: a few hundred per season until
    2019/20 (hand-picked specimens, positivity around 50 %), none reported in 2020/21, tens of
    thousands since 2021/22. Positivity is comparable only from 2021/22 on (registry entry
    `flunet-jmenovatel-2021`).
  - FluNet reports two independent systems for the same week, `SENTINEL` (about 50 specimens) and
    `NONSENTINEL` (about 2,000). Do not add them up. The `zdroj` column keeps the distinction.
  - The non-sentinel FluNet rows are the same numbers as the SZÚ weekly PDFs for 2024/25 and
    2025/26, and they also carry the number of specimens tested — the denominator of positivity.
    For 2022/23 and 2023/24 the season sums differ from the SZÚ totals by up to ~130 per virus
    (see the SZÚ seasonal archives above).
  - FluID gives a rate that is comparable across years, which makes it the input of the seasonal
    thresholds.

### ECDC — ERVISS

- **Where:** the GitHub repository `EU-ECDC/Respiratory_viruses_weekly_data`, updated weekly.
- **Scraper:** `ecdc_erviss.py`
- **Output:**
  - `ecdc/erviss_ili_ari_cz.csv` — `tyden_iso, ukazatel (ILI/ARI), vek, mira_na_100k`
  - `ecdc/erviss_nonsentinel_cz.csv` — `tyden_iso, patogen, typ, subtyp, ukazatel, vek, hodnota`
- **Checks:** expected columns, rows for Czechia, no unknown indicator, newest week not older than
  45 days.
- **Good to know:**
  - The Czech history starts in 2022-W25, so ERVISS alone is not enough for thresholds. It
    supplies the most recent weeks on top of the FluID history — the same series reported through
    two routes.
  - The laboratory file carries tests and detections for influenza, RSV and SARS-CoV-2
    (SARS-CoV-2 also by age group). Influenza tests equal the FluNet non-sentinel figures in every
    week, and detections do from 2022-W37 on; in 2022-W25–W36 ERVISS shows 4–29 influenza
    detections a week where FluNet shows 0–2, and those weeks are not used.
  - The number of specimens tested for RSV is reported only in some periods: 2022-W25–W36,
    2024-W01–W30 and continuously since 2025-W01 (registry entry `erviss-lab-rsv-jmenovatel`).
  - SARS-CoV-2 changes its denominator in 2026-W34: until then thousands to tens of thousands of
    specimens a week, from then the same number as for influenza (about 300) with 0–1
    detections. The positivity chart stops at 2026-W33 (registry entry
    `erviss-sars-cov-2-jmenovatel-2026-08`).
    The break does not line up with the TESSy → EpiPulse Cases transition: weeks 2026-W21–W33,
    published after it, continue the earlier series; the same week the PCR count in the MZČR
    data fell too.
  - **Update status (checked 2026-10-25).** Since 26 June 2026 the README says that GitHub
    updates are paused after the move from TESSy to EpiPulse Cases. In fact the repository has a
    weekly `commit_as_of_<date>` again since 2026-08-07; the gap in `data/snapshots/` runs from
    2026-05-22 to 2026-08-07. The newest Czech week on 2026-10-25 was 2026-W42 (commit of
    2026-10-23); the server pipeline was one weekly commit behind (snapshot 2026-10-16), which is
    its normal cadence.

### ECDC — ERVISS snapshots

- **Where:** the same repository, folder `data/snapshots/`: every Friday ECDC stores each file as
  it was that day (since 2023-11-24).
- **Scraper:** `ecdc_erviss_snapshots.py`, an optional job in `run_all.py` — its failure does not
  count towards the exit code.
- **What it does:** lists the folder through the GitHub tree API, downloads the
  `nonSentinelTestsDetections` snapshots not yet present and keeps the Czech rows. Published
  snapshots do not change, so nothing is downloaded twice. While the newest local snapshot is
  less than 6 days old the API is not called at all (the anonymous limit is 60 requests an hour).
  The first fill, about 1 GB, runs in batches of 40 snapshots per run.
- **Output:** `ecdc/erviss_snapshots/<YYYY-MM-DD>_nonSentinelTestsDetections.csv.gz`, original
  columns. Not passed to the `raw/` archive — the files are an immutable dated history already.
- **Good to know:** this is the reporting triangle for the nowcast
  ([analytics/nowcasting.md](analytics/nowcasting.md)). The non-sentinel detections equal the SZÚ
  weekly numbers. Since 2024 there is no total per influenza type, only subtypes.

### ECDC — RespiCast

- **Where:** the GitHub repository `european-modelling-hubs/RespiCast-SyndromicIndicators`, one
  file per weekly forecast round since October 2024.
- **Scraper:** `ecdc_respicast.py`
- **What it does:** lists the rounds through the GitHub API, downloads the hub ensemble
  (`respicast-hubEnsemble`) and the hub's reference model (`respicast-quantileBaseline`), keeps the
  quantile rows for Czechia and the ILI and ARI targets. A published round never changes, so rounds
  are cached by file name in `ecdc/respicast_cache/`.
- **Output:** `ecdc/respicast_cz.csv` — `model, kolo, ukazatel, tyden_do, horizont, kvantil,
  hodnota`
- **Checks:** expected columns, newest ensemble round not older than 45 days, the newest round must
  contain a forecast for Czechia.
- **Good to know:**
  - The whole history of rounds is kept, not just the latest one; without it past forecasts cannot
    be scored.
  - Horizon 1 is not the future but the week that has just ended and has no consolidated data yet.
  - Without `GITHUB_TOKEN` the GitHub API allows 60 requests per hour per IP address.
  - The repository states no licence — clarify the terms with the hub before showing the forecast
    publicly.

### ECDC — COVID-19 (frozen)

- **Where:** `opendata.ecdc.europa.eu`, daily national cases and deaths.
- **Scraper:** `ecdc_covid.py` — filters `geoId = CZ`.
- **Output:** `ecdc/ecdc_covid_cz.csv` — `datum, nove_pripady, nove_umrti, populace`
- **Good to know:** ECDC stopped publishing this dataset in autumn 2022. The scraper is kept for
  the historical series; no chart reads the file today. Once `ecdc_covid_cz.csv` exists it is not
  downloaded again (PPDB-73): on 30 September 2026 the ECDC server started resetting connections,
  and because `run_all.py` fails when any source fails, this archive-only source stopped the
  whole pipeline. To fetch it again, delete the file.

## Licences

Licences are recorded per source in `sources.yaml`, written the way the source states them. Where
a source states none, the registry says so instead of guessing. For the ÚZIS datasets the licence
claimed by the publisher's own CSVW description is fetched on every run — see
[metadata.md](metadata.md).

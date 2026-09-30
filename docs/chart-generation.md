# Chart generation — from CSV to JSON

`scripts/generate_json.py` turns the downloaded CSV files into the JSON files the portal's charts
read. Two more scripts write one larger JSON each: `detect_anomalies.py` and `compute_mem.py`;
they are described in [analytics/](analytics/).

```bash
python scripts/generate_json.py
```

Input comes from `$DATA_DIR`, output goes to `$OUTPUT_DIR`. One file per chart, named after the
chart: `$OUTPUT_DIR/<chart name>.json`.

## How one chart is built

Every chart is one function, and they all follow the same four steps:

1. **Read** the CSV with pandas. If the file is missing, print a message and return — the other
   charts are still generated.
2. **Filter and aggregate** — pick the rows, group by period / region / age, sum.
3. **Build the Chart.js shape** — a list of `labels` for the X axis and one `datasets` entry per
   series. The helper `ds(label, data, color, chart_type)` fills in the colours and line settings.
4. **Save** with `save(name, obj)`, which attaches the `meta` block and writes the file.

A complete example — weekly COVID-19 cases and deaths:

```python
def covid_cases_weekly():
    df = pd.read_csv(DATA_DIR / "mzcr" / "covid_pripady.csv")      # 1. read
    df = to_weekly(df)                                             # 2. add a week column…
    w = df.groupby("week").agg(                                    #    …and sum per week
        pripady=("prirustkovy_pocet_nakazenych", "sum"),
        umrti=("prirustkovy_pocet_umrti", "sum"),
    ).reset_index()
    labels = w["week"].dt.strftime("%Y-%m-%d").tolist()
    save("covid_cases_weekly", {                                   # 3. + 4. shape and save
        "labels": labels,
        "datasets": [
            ds("Nové případy (týden)", w["pripady"].tolist(), "blue", "bar"),
            ds("Úmrtí (týden)", w["umrti"].tolist(), "red", "line"),
        ],
    })
```

## The shape of the output

Three shapes are used.

**Series chart** — most files:

```jsonc
{
  "labels": ["2020-03-02", "2020-03-09", "…"],
  "datasets": [
    { "label": "Nové případy (týden)", "data": [12, 87, "…"], "type": "bar",
      "backgroundColor": "rgba(13,110,253,0.6)", "borderColor": "rgb(13,110,253)",
      "borderWidth": 2, "fill": false, "tension": 0.3, "pointRadius": 1 }
  ],
  "meta": { "…": "see metadata.md" }
}
```

Some series charts carry extra top-level keys for the page: `season`, `x_title`, `unit`, `obdobi`
(the period covered), `neznamy_vek_pripady` (cases with unknown age).

**Map** — values per region:

```jsonc
{
  "year": 2025, "population_year": 2025,
  "unit": "případů na 100 000 obyvatel",
  "regions": { "CZ010": 720.6, "CZ020": 655.1, "…": 0 },
  "labels":  { "CZ010": "Praha", "CZ020": "Středočeský", "…": "" },
  "meta": { "…": "" }
}
```

**Summary tile** — plain keys and values (`covid_summary.json`).

Series labels and titles are Czech; the portal shows them as they are.

## The `meta` block

`save()` calls `chart_meta.attach(name, obj)` before writing. The block says what the chart
measures, in which unit, for which period, from which source, how fresh that source is and which
caveats apply. The frontend ignores it; it is there for machines. How it is assembled is described
in [metadata.md](metadata.md).

A new chart must get a line in `charts.yaml`. Without it the chart is still written, but without
`meta`, and `tests/test_source_metadata.py` fails.

## The database path

`generate_json.py` can read PostgreSQL through `db_query(sql)`. The function returns a DataFrame,
or `None` when the database is unreachable, empty, or `psycopg` is not installed. `None` means
"use the files", not "error".

SQL is used where it earns its place — a join across two sources — not everywhere. Today that is
one chart, `isin_regional_incidence` (ISIN cases × ČSÚ population). Both paths must give the same
JSON; for instance the SQL path converts PostgreSQL `NUMERIC` values to `float`, otherwise they
would be serialised as strings.

## Catalogue of generated files

### COVID-19 — source MZČR

| File | Function | Input | Content |
|---|---|---|---|
| `covid_cases_weekly` | `covid_cases_weekly` | `covid_pripady.csv` | new cases and deaths per week |
| `covid_hospitalization` | `covid_hospitalization` | `covid_hospitalizace.csv` | patients in hospital, ICU, on ventilation and ECMO — weekly maximum |
| `covid_testing` | `covid_testing` | `covid_testy.csv` | PCR positivity in percent per week |
| `covid_incidence` | `covid_incidence` | `covid_incidence.csv` | 7-day incidence per 100,000, every seventh day |
| `covid_summary` | `covid_summary` | `covid_pripady.csv` | cumulative cases, deaths and tests, and the date of the last record |
| `covid_by_age` | `covid_by_age` | `covid_osoby_agg.csv`, `covid_umrti.csv` | cases and deaths by ten-year age group; the number with unknown age is reported separately |
| `covid_cfr_by_age` | `covid_by_age` | same | case fatality rate by age group, in percent |
| `covid_by_vaccination` | `covid_by_vaccination` | `covid_vax_pozitivni.csv`, `covid_vax_hospitalizace.csv` | cases and hospitalisations by vaccination status |
| `covid_hosp_rate_by_vax` | `covid_by_vaccination` | same | hospitalisation rate by vaccination status, in percent |

### Influenza and respiratory viruses — source SZÚ

| File | Function | Input | Content |
|---|---|---|---|
| `flu_season_overview` | `flu_season_overview` | `szu_influenza_*.csv` | influenza A and B detections per season |
| `flu_respiratory_all` | `flu_respiratory_all` | `szu_influenza_*.csv` | ten respiratory viruses per season |
| `flu_weekly` | `flu_weekly` | `szu_weekly_viry.csv` | weekly detections in the running season: influenza A, influenza B, RSV, SARS-CoV-2 |
| `flu_regional_weekly` | `flu_regional_weekly` | `szu_weekly_kraje.csv` | weekly positive detections in the six regions with the most detections |
| `flu_regional_overview` | `flu_regional_overview` | `szu_weekly_kraje.csv` | positive detections and specimens tested per region |
| `flu_positivity_seasons` | `flu_positivity_seasons` | `who_flunet_cz.csv` | influenza positivity in percent by week of the season (40–20), one series per season since 2021/22 |
| `flu_positivity_weekly` | `flu_positivity_weekly` | `erviss_nonsentinel_cz.csv` | weekly positivity of influenza and RSV, each with its own denominator |

Only rows of the category "Detekce viru" (virus detection) are used from the season files;
serology and isolation are left out.

#### Positivity

Counts of detections grow with the amount of testing (about 440 specimens in the non-sentinel
system in 2019/20, about 65,000 in 2024/25), so "more influenza this year" cannot be read from
them. Positivity — detections divided by specimens tested — cancels the testing volume out.

Rules shared by both charts:

- **Non-sentinel only** in the FluNet chart. The sentinel system has 20–50 specimens a week, and
  the two systems must not be added up.
- **Seasons from 2021/22**, at most the last six. Earlier denominators are not comparable.
- **Empty detections inside a series are zero, at its end unknown.** The sources stopped writing
  zeros in 2025; the last weeks have tests reported before results. Trailing weeks without any
  result are dropped.
- **No percentage below 30 specimens** (`POSITIVITY_MIN_TESTS`); the value is `null`.
- **Week 53 is pooled with week 52** (numerator and denominator), so every season has the same
  axis.
- **The weekly chart starts at the last gap in a denominator.** ERVISS reports the number of
  specimens tested for RSV only in some periods; the chart takes the last unbroken period in which
  both viruses have a denominator (today from 2025-W01), and never goes before 2022-W37.

Both files carry the raw numbers next to the percentages: `tests` and `detections`, keyed by
series label, aligned with `labels`. `flu_positivity_seasons` uses the grain `season_week` in
`charts.yaml` — its X axis is the week of the season, not a timeline, so no period is read from
it.

### Notifiable infectious diseases — source ÚZIS ISIN

| File | Function | Input | Content |
|---|---|---|---|
| `isin_top_diseases` | `isin_top_diseases` | ISIN CSV | yearly cases of the ten most frequent diagnoses |
| `isin_monthly_trend` | `isin_monthly_trend` | ISIN CSV | monthly cases of four selected diseases: varicella, salmonella infections, pertussis, other spirochaetal infections (the ICD group that contains Lyme disease) |
| `isin_age_groups` | `isin_age_groups` | ISIN CSV | cases by age group, all years |
| `isin_regional_map` | `isin_regional_map` | ISIN CSV | cases per region in the last available year |
| `isin_regional_incidence` | `isin_regional_incidence` | PostgreSQL, or ISIN CSV + `population.csv` | the same per 100,000 inhabitants |
| `isin_group_<key>` | `isin_disease_groups` | ISIN CSV | yearly cases of each disease in a thematic group |
| `isin_group_other` | `isin_disease_groups` | ISIN CSV | yearly total of everything not assigned to a group |

The thematic groups are defined in `DISEASE_GROUPS` in `generate_json.py`:
`childhood_airborne`, `gastrointestinal`, `skin_contact`, `vector_animal`, `hepatitis`, `sti`,
`rare_severe`. Influenza is excluded from ISIN charts on purpose — the SZÚ source is more
detailed.

### STI and tuberculosis registries — source ÚZIS RPN, RTBC

| File | Function | Input | Content |
|---|---|---|---|
| `sti_registry_trend` | `sti_registry` | `uzis_pohlavni_nemoci.csv` | yearly cases of syphilis, gonorrhoea and LGV |
| `sti_registry_incidence` | `sti_registry` | + `population.csv` | syphilis and gonorrhoea per 100,000 |
| `sti_registry_sex` | `sti_registry` | + `population.csv` | the same for men and women, each with its own denominator |
| `sti_registry_map` | `sti_registry` | + `population.csv` | incidence per region in the last year |
| `sti_registry_age` | `sti_registry` | `uzis_pohlavni_nemoci.csv` | cases by age group over the last five years |
| `tbc_incidence` | `tbc_registry` | `uzis_tuberkuloza.csv` + `population.csv` | tuberculosis per 100,000 per year |
| `tbc_map` | `tbc_registry` | + `population.csv` | incidence per region, averaged over the last three years |
| `tbc_origin` | `tbc_registry` | `uzis_tuberkuloza.csv` | cases by country of birth: Czechia or abroad |
| `tbc_age` | `tbc_registry` | `uzis_tuberkuloza.csv` | yearly cases by age group |

Notes:

- The four syphilis codes (A50–A53) are summed into one series — the trend is about the disease,
  not the stage in which it was caught.
- The tuberculosis map averages three years because about 30 cases per region and year are mostly
  noise.
- Years without a population figure are left out of incidence charts; they are not estimated.

### Analytics

| File | Script | Content | Documentation |
|---|---|---|---|
| `anomaly_signals` | `detect_anomalies.py` | the series whose last month exceeds the expected level | [analytics/anomaly-detection.md](analytics/anomaly-detection.md) |
| `flu_mem` | `compute_mem.py` | influenza thresholds, trend and forecast for ILI and ARI | [analytics/flu-mem.md](analytics/flu-mem.md) |

## Conventions

- **A missing input skips the chart**, it does not fail the run.
- **Missing values are reported, not dropped silently.** Records without an age stay out of the
  age chart, but their number is written into the JSON so the page can state it.
- **Rates need denominators.** Anything "per 100,000" is computed with `population.csv`; when the
  population of the same year is not published yet, the newest available year is used and recorded
  as `population_year`.
- **File names are an interface.** The portal's pages refer to the files by name. Renaming a chart
  is a breaking change — see the contract in the [README](../README.md#contract-with-the-portal).

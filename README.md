# pathogensportal-db

Datová pipeline pro [Pathogen Portal CZ](https://pathogens.vm.cesnet.cz/) — stahuje otevřená
epidemiologická data (MZČR, ECDC, SZÚ, ÚZIS ISIN, IMG AV ČR) a generuje z nich Chart.js JSON
podklady pro portálové dashboardy.

## Struktura repa

```
scripts/                 pipeline skripty
scripts/scrapers/         jednotlivé scrapery (MZCR, ECDC, SZÚ, ÚZIS ISIN, Ebola/Google Drive)
scripts/run_all.py        spustí všechny scrapery, uloží CSV do $DATA_DIR
scripts/generate_json.py  přečte stažená data, vygeneruje Chart.js JSON do $OUTPUT_DIR
scripts/process_ebola.py  zpracuje nejnovější Ebola ZIP na chart JSON + Hugo stránky
db/init.sql               schéma PostgreSQL (portál si ho mountuje do kontejneru pathogen-db)
Dockerfile                image `datascrapper` — portál ho staví přímo z tohohle repa
requirements.txt          Python závislosti (jediný zdroj — nic jiného se nepoužívá)
.env.example               vzor proměnných prostředí pro lokální běh
```

## Proměnné prostředí

| Proměnná | Výchozí | Význam |
|---|---|---|
| `DATA_DIR` | `./data` | kam scrapery (vč. Ebola) ukládají stažená data a odkud je čte `generate_json.py`/`process_ebola.py` |
| `OUTPUT_DIR` | `./site/static/data/charts` | kam `generate_json.py`/`process_ebola.py` píší vygenerovaný chart JSON |
| `CONTENT_DIR` | `./site/content/cs/dashboards` | kam `process_ebola.py` píše Hugo Markdown stránky (česky) |

Zkopíruj `.env.example` na `.env` a uprav podle potřeby. Bez nastavených proměnných se použijí
výchozí hodnoty výše (relativně ke kořeni repa).

## Jak to spustit lokálně

```bash
pip install -r requirements.txt
python scripts/run_all.py             # stáhne CSV do $DATA_DIR
python scripts/scrapers/gdrive_ebola.py  # stáhne nejnovější Ebola ZIP do $DATA_DIR/ebola
python scripts/generate_json.py       # vygeneruje chart JSON do $OUTPUT_DIR
python scripts/process_ebola.py       # vygeneruje Ebola chart JSON + Markdown do $OUTPUT_DIR/$CONTENT_DIR
```

## Jak to spustit v Dockeru

```bash
docker build -t pathogensportal-db .
docker run --rm \
  -v "$PWD/data:/data" \
  -v "$PWD/out/charts:/output/charts" \
  -v "$PWD/out/content:/output/content" \
  pathogensportal-db
```

`CMD` v Dockerfilu spustí celý řetězec za sebou: `run_all.py` → `gdrive_ebola.py` →
`generate_json.py` → `process_ebola.py`.

## Jak repo konzumuje portál

Portál (`pathogensportal`) si tenhle repo bere jako **git submodule pinnutý na release tag**
(ne na branch — jinak by každý push sem měnil to, co běží v produkci) a staví z něj image
`datascrapper`. Praktický důsledek: **jména skriptů (`run_all.py`, `generate_json.py`,
`process_ebola.py`), `CMD` v Dockerfilu, jména proměnných (`DATA_DIR`/`OUTPUT_DIR`/`CONTENT_DIR`)
a cesta `db/init.sql` jsou veřejné API tohohle repa vůči portálu.** Jejich změna je breaking
change, ne interní úprava — vyžaduje novou verzi (release) a poznámku v release notes, ne
tichý push na `dev`.

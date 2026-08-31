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
| `DATA_DIR` | `./data` | kam scrapery ukládají stažená CSV a odkud je čte `generate_json.py` |
| `OUTPUT_DIR` | `./site/static/data/charts` | kam `generate_json.py` píše vygenerovaný chart JSON |

Zkopíruj `.env.example` na `.env` a uprav podle potřeby. Bez nastavených proměnných se použijí
výchozí hodnoty výše (relativně ke kořeni repa).

## Jak to spustit lokálně

```bash
pip install -r requirements.txt
python scripts/run_all.py        # stáhne CSV do $DATA_DIR
python scripts/generate_json.py  # vygeneruje chart JSON do $OUTPUT_DIR
```

## Jak to spustit v Dockeru

```bash
docker build -t pathogensportal-db .
docker run --rm -v "$PWD/data:/data" -v "$PWD/out:/output/charts" pathogensportal-db
```

## Jak repo konzumuje portál

Portál (`pathogensportal`) si tenhle repo bere jako **git submodule pinnutý na release tag**
(ne na branch — jinak by každý push sem měnil to, co běží v produkci) a staví z něj image
`datascrapper`. Praktický důsledek: **jména skriptů (`run_all.py`, `generate_json.py`), `CMD`
v Dockerfilu, jména proměnných (`DATA_DIR`/`OUTPUT_DIR`) a cesta `db/init.sql` jsou veřejné API
tohohle repa vůči portálu.** Jejich změna je breaking change, ne interní úprava — vyžaduje novou
verzi (release) a poznámku v release notes, ne tichý push na `dev`.

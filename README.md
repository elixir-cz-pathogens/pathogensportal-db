# pathogensportal-db

Datová pipeline pro [Pathogen Portal CZ](https://pathogens.vm.cesnet.cz/). Stahuje otevřená
epidemiologická data, archivuje je, normalizuje do PostgreSQL a generuje z nich Chart.js podklady
a Hugo stránky pro portálové dashboardy.

Repo je samostatné schválně: portál je statický web, tenhle repo je všechno, co se hýbe kolem dat.
Portál si ho bere jako git submodule pinnutý na release tag — viz [Jak repo konzumuje
portál](#jak-repo-konzumuje-portál).

## Datové zdroje

| Zdroj | Co z něj bereme | Poznámka |
|---|---|---|
| **MZČR** — otevřená data COVID-19 | případy, hospitalizace, testy, incidence po krajích | API v2, aktualizace denně |
| **ÚZIS ISIN** | hlášená infekční onemocnění (114 diagnóz) po krajích, měsících a věkových skupinách | hlavní zdroj pro dashboardy infekčních nemocí |
| **SZÚ** | týdenní hlášení akutních respiračních infekcí a chřipky | PDF zprávy (historické sezóny) + průběžně aktualizovaná stránka aktuální sezóny |
| **ČSÚ** | počty obyvatel po krajích (dataset `PORKR01`) | jmenovatele — bez nich jsou z čísel počty, ne incidence |
| **ECDC** | historická data COVID-19 pro ČR | zdroj **přestal publikovat na podzim 2022**; scraper zůstává kvůli historické řadě |
| **Ebola (IMG AV ČR)** | kurátorovaná denní řada aktuální epidemie v DRK + obsah stránek | ZIP balíčky přes Google Drive |

## Jak to funguje

Pipeline má čtyři fáze, které na sebe navazují přes souborový systém, ne přes sdílený stav:

```
1. stažení      run_all.py ─────────► $DATA_DIR/<zdroj>/*.csv
                gdrive_ebola.py ────► $DATA_DIR/ebola/*.zip

2. archivace    snapshot.py ────────► $DATA_DIR/raw/<datum>/*.gz + manifest.json

3. normalizace  load_to_db.py ──────► PostgreSQL: observation, population

4. výstup       generate_json.py ───► $OUTPUT_DIR/*.json      (Chart.js)
                process_ebola.py ───► $OUTPUT_DIR/*.json
                                    + $CONTENT_DIR/*.md       (Hugo stránky)
```

Fáze jsou samostatně spustitelné a **idempotentní** — opakovaný běh nic nezduplikuje ani
nerozbije. Každý scraper běží v izolaci: když jeden zdroj spadne (nedostupný web, změněný formát),
ostatní doběhnou a `run_all.py` na konci vypíše, co se nepovedlo.

### Archivace snímků

`snapshot.py` ukládá každé stažení jako datovaný gzip a vede manifest se sha256 otisky. Soubor,
který se od minule nezměnil, se neukládá znovu. Důvod není úspora místa, ale **reprodukovatelnost**:
bez archivu nejde zpětně říct, jaká data stála za grafem publikovaným v minulosti, a otevřené zdroje
svá historická čísla běžně tiše opravují.

### Datová vrstva

Všechny zdroje měří v zásadě totéž — *kolik případů něčeho bylo za nějaké období, na nějakém území,
v nějaké skupině*. Proto jedna tabulka `observation` pro všechny, ne tabulka na zdroj: dotaz napříč
zdroji pak nepotřebuje `UNION`.

```sql
observation (source_id, diagnosis_name, region_code, age_group, sex,
             period_start, period_end, metric, value, snapshot_date)
population  (region_code, age_group, sex, year, value)
```

Dvě věci na tom schématu stojí za vysvětlení:

- **`snapshot_date` je součástí unikátního klíče.** Pipeline data nepřepisuje, ale přidává novou
  verzi téhož pozorování. Vzniká tím *reporting triangle* — záznam o tom, jak se čísla za dané období
  postupně doplňovala dodatečnými hlášeními. Bez něj nejde spočítat nowcasting, tedy korekci
  reportovacího zpoždění.
- **Unikátní klíč je `NULLS NOT DISTINCT`.** `NULL` tu znamená „nerozlišeno podle téhle dimenze"
  (ISIN neagreguje podle pohlaví, takže `sex` je vždy `NULL`). Ve výchozím chování Postgresu se dva
  `NULL` nepovažují za shodné, klíč by nikdy nesedl, `ON CONFLICT` by se nespustil a **každý běh
  loaderu by data zduplikoval**. Vyžaduje PostgreSQL 15+.

`load_to_db.py` si schéma zajistí sám — aplikuje `db/init.sql`. Postgres totiž spustí skripty
z `docker-entrypoint-initdb.d` jen při první inicializaci prázdného svazku, takže na databázi, která
už jednou běžela, by tabulky přidané později nikdy nevznikly. Schéma se schválně neopisuje do Pythonu:
`db/init.sql` zůstává jediným zdrojem pravdy a je celý idempotentní.

### Generování výstupů

`generate_json.py` čte primárně z databáze a při její nedostupnosti spadne zpátky na CSV, takže
pipeline funguje i bez běžícího Postgresu. Generuje přes dvacet datových sad — COVID-19 (průběh,
hospitalizace, testování, věk, vakcinační status), chřipku a ARI (sezónní i krajské přehledy)
a infekční nemoci z ISIN (skupiny diagnóz, krajská incidence, měsíční trendy, věkové skupiny).

`process_ebola.py` je zvláštní případ: rozbalí nejnovější ZIP, vygeneruje z kurátorované časové řady
grafy a převede přiložené HTML na Hugo stránky.

## Struktura repa

```
scripts/run_all.py            spustí všechny scrapery, uloží CSV do $DATA_DIR
scripts/snapshot.py           datované gzip snímky staženého se sha256 deduplikací
scripts/load_to_db.py         ETL: CSV → PostgreSQL (observation, population)
scripts/generate_json.py      přečte data, vygeneruje Chart.js JSON do $OUTPUT_DIR
scripts/process_ebola.py      zpracuje nejnovější Ebola ZIP na chart JSON + Hugo stránky
scripts/scrapers/             jednotlivé scrapery (MZČR, ECDC, SZÚ, ÚZIS ISIN, ČSÚ, Ebola)
db/init.sql                   schéma PostgreSQL (portál si ho mountuje do kontejneru pathogen-db)
Dockerfile                    image `datascrapper` — portál ho staví přímo z tohohle repa
requirements.txt              Python závislosti (jediný zdroj — nic jiného se nepoužívá)
.env.example                  vzor proměnných prostředí pro lokální běh
```

## Proměnné prostředí

| Proměnná | Výchozí | Význam |
|---|---|---|
| `DATA_DIR` | `./data` | kam scrapery ukládají stažená data a odkud je čtou další fáze |
| `OUTPUT_DIR` | `./site/static/data/charts` | kam se píše vygenerovaný chart JSON |
| `CONTENT_DIR` | `./site/content/cs/dashboards` | kam `process_ebola.py` píše Hugo stránky (česky) |
| `DB_HOST` | `localhost` | databáze pro `load_to_db.py` a čtení v `generate_json.py` |
| `DB_PORT` | `5432` | |
| `POSTGRES_DB` | `pathogens` | |
| `POSTGRES_USER` | `portal` | |
| `POSTGRES_PASSWORD` | `portal_dev` | jen pro lokální vývoj — v produkci jde ze secretů portálu |

Zkopíruj `.env.example` na `.env` a uprav podle potřeby. Bez nastavených proměnných se použijí
výchozí hodnoty výše (relativně ke kořeni repa).

## Jak to spustit lokálně

```bash
pip install -r requirements.txt

python scripts/run_all.py                # stáhne CSV do $DATA_DIR (a udělá snímek)
python scripts/scrapers/gdrive_ebola.py  # stáhne nejnovější Ebola ZIP do $DATA_DIR/ebola
python scripts/load_to_db.py             # naplní PostgreSQL (volitelné, viz níže)
python scripts/generate_json.py          # vygeneruje chart JSON do $OUTPUT_DIR
python scripts/process_ebola.py          # Ebola grafy do $OUTPUT_DIR + stránky do $CONTENT_DIR
```

Krok s databází je volitelný — bez něj `generate_json.py` čte CSV napřímo. Databáze je potřeba
na analytiku napříč zdroji (baseline, incidence, reporting triangle), ne na vykreslení grafu.

```bash
python scripts/load_to_db.py --dry-run              # jen spočítá řádky, nic nezapíše
python scripts/load_to_db.py --snapshot-date 2026-01-31   # načte pod konkrétním datem snímku
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

Nová data se na portálu objeví až tehdy, když se **submodul přepne na nový tag**. Samotné vydání
verze tady portálem nehne.

## Změny oproti verzi 0.1.0

### Datová vrstva (nová)

- **Archivace snímků** — každé stažení se ukládá jako datovaný gzip s manifestem a sha256
  deduplikací, takže lze zpětně doložit, z jakých dat vznikl publikovaný graf.
- **PostgreSQL jako normalizovaná vrstva** — tabulky `observation` a `population`, jedno schéma
  pro všechny zdroje, `snapshot_date` v unikátním klíči kvůli reporting triangle.
- **Loader si zajistí schéma sám** aplikací `db/init.sql`, takže funguje i proti databázi, která už
  jednou běžela a `docker-entrypoint-initdb.d` se na ní znovu nespustí.
- **Oprava duplikace dat při opakovaném běhu** — unikátní klíč obsahuje sloupce, které jsou u části
  zdrojů `NULL`; ve výchozím chování Postgresu klíč nesedl, `ON CONFLICT` se nespouštěl a data se
  s každým během zdvojovala. Řeší `UNIQUE NULLS NOT DISTINCT`.
- **`generate_json.py` čte z databáze** a při její nedostupnosti spadne zpátky na CSV.

### Nové zdroje a rozšíření stávajících

- **ČSÚ populace** — nový scraper pro počty obyvatel po krajích, díky kterému lze počítat
  **incidenci na 100 000 obyvatel** místo holých počtů. Absolutní čísla samotná kraje řadí podle
  velikosti, ne podle epidemiologické situace.
- **SZÚ: automatické stahování aktuální sezóny** vedle historických PDF, plus doplnění chybějících
  sezón zpětně.
- **ISIN: skupiny diagnóz** — místo žebříčku deseti nejčastějších nemocí jsou diagnózy roztříděné
  do věcných skupin (dětské a vzdušné nákazy, střevní, kožní, přenášené klíšťaty a zvířaty,
  hepatitidy, pohlavně přenosné, vzácné závažné a ostatní).

### Opravy scraperů

- Chybné určení sezóny u SZÚ (hranice sezóny je 40. týden) a prohozené pořadí roku a týdne
  v třídicím klíči.
- Ztráta dat u PDF buněk obsahujících víc virů najednou — brala se jen první hodnota.
- Rate limiting u stahování z Google Drive — přepracováno na manifest identifikátorů, takže se už
  stažené soubory nestahují znovu, a selhání jednoho souboru neshodí zbytek.
- Chybějící nástroj v Docker image, kvůli kterému se část grafů tiše negenerovala.

### Grafy a stránky

- **Věkové kohorty COVID-19**: záznamy bez vyplněného roku narození se slévaly do jedné nesmyslné
  kohorty na začátku grafu. Nově se z grafu vyřazují, ale jejich počet a podíl se **pojmenovaně
  uvádí** — tiše zahodit osminu případů by bylo zavádějící.
- **Trajektorie ebolavirových epidemií**: řada aktuální epidemie se počítá z vlastní časové řady
  místo ručně vypsaných hodnot (nemůže tedy zastarat), osa X je číselná místo kategorické (aby
  sklony křivek odpovídaly skutečné rychlosti růstu) a osa Y logaritmická (epidemie se liší
  o řády).
- **Tabulka hodnot u Eboly** se generuje staticky. Zdrojové HTML má tělo tabulky prázdné a plnil ho
  JavaScript z přiloženého CSV, který se na portál nepřenáší — tabulka proto zůstávala prázdná.
- Souhrnné dlaždice u Eboly se dopočítávají ze stejné časové řady jako grafy, takže se s nimi
  nemohou rozejít.

### Prostředí a dokumentace

- Sjednocený kontrakt proměnných `DATA_DIR` / `OUTPUT_DIR` / `CONTENT_DIR` s portálem.
- Dockerfile a `requirements.txt` jako jediný zdroj závislostí.
- Tenhle README.

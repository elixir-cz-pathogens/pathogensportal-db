FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cesty jsou konfigurovatelné — kontejner je tím pádem přenositelný.
# Jména proměnných odpovídají .env.example i deploy/docker-compose.yml v portál-repu
# (DATA_DIR / OUTPUT_DIR).
ENV DATA_DIR=/data \
    OUTPUT_DIR=/output/charts

# Stáhne data a vygeneruje chart JSON + signály detekce anomálií
# (běh na vyžádání / z cronu):
#   docker compose --profile tools run --rm datascrapper
#
# ⚠️ Pipeline už NEPÍŠE žádné Hugo stránky. Ebola část (gdrive_ebola.py +
# process_ebola.py) byla odstraněna v PPDB-53 — obsah i grafy k ebole nově dodává
# AI agent jako pull request přímo do portálu. Proto tu není ani CONTENT_DIR:
# nic do něj nezapisuje. Portál ho může dál mountovat, kontejner si ho nevšimne.
#
# Nowcast je za `||`: jeho selhání nesmí zastavit obnovu dat (grafy se vygenerují
# i bez něj, chybí jen flu_nowcast.json). Viz výpadek 6.–9. 10. 2026 v CHANGELOG.
# compute_resp.py (respirační bloky) běží po compute_mem, protože z flu_mem.json
# bere práh a trend; stejně jako nowcast nesmí zastavit obnovu ostatních dat.
# compute_genomics.py (genomická data) nezávisí na ničem jiném než na svých
# scraperech; i ono je za `||`, aby výpadek cizího archivu nezastavil obnovu grafů.
# compute_isin.py (přehled infekčních nemocí) běží po generate_json.py, protože
# větu ke krajské mapě bere z isin_regional_incidence.json; i ono je za `||`.
CMD ["sh", "-c", "python scripts/run_all.py && python scripts/generate_json.py && python scripts/detect_anomalies.py && python scripts/compute_mem.py && (python scripts/compute_nowcast.py || echo 'flu_nowcast: selhal, pokračuji bez něj') && (python scripts/compute_resp.py || echo 'compute_resp: selhal, pokračuji bez něj') && (python scripts/compute_genomics.py || echo 'compute_genomics: selhal, pokračuji bez něj') && (python scripts/compute_isin.py || echo 'compute_isin: selhal, pokračuji bez něj')"]

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
CMD ["sh", "-c", "python scripts/run_all.py && python scripts/generate_json.py && python scripts/detect_anomalies.py"]

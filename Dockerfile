FROM python:3.12-slim

WORKDIR /app

# generate_json.py volá regionální/věkové dotazy přes systémové sqlite3 CLI
# (subprocess, ne Python modul sqlite3) — bez něj FileNotFoundError spadne
# hned na první takové funkci a nedoběhnou ani ISIN grafy za ní.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cesty jsou konfigurovatelné — kontejner je tím pádem přenositelný.
# Jména proměnných odpovídají .env.example i deploy/docker-compose.yml v portál-repu
# (DATA_DIR / OUTPUT_DIR).
ENV DATA_DIR=/data \
    OUTPUT_DIR=/output/charts

# Stáhne data a vygeneruje chart JSON (běh na vyžádání / z cronu):
#   docker compose --profile tools run --rm datascrapper
CMD ["sh", "-c", "python scripts/run_all.py && python scripts/generate_json.py"]

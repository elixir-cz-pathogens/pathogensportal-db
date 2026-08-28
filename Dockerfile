FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cesty jsou konfigurovatelné — kontejner je tím pádem přenositelný.
# Jména proměnných odpovídají .env.example (DATA_IN / DATA_OUT).
ENV DATA_IN=/data \
    DATA_OUT=/output/charts

# Stáhne data a vygeneruje chart JSON (běh na vyžádání / z cronu):
#   docker compose --profile tools run --rm datascrapper
CMD ["sh", "-c", "python scripts/run_all.py && python scripts/generate_json.py"]

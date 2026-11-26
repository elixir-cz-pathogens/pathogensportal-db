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
# detect_anomalies.py --zdroj tydenni (signály 11 diagnóz z týdenního souhrnu ÚZIS)
# je vedlejší výstup: bez staženého souhrnu nic nezapíše a jeho pád nesmí zastavit
# hlavní signály ani ostatní grafy, proto je také za `||`.
# compute_esurv.py (denní e-surveillance ÚZIS: případy, nemocnice, očkování) čte jen
# soubory svého scraperu; bez nich nic nezapíše a i ono je za `||`.
# last_checked.py (datum posledního ověření u zdroje) musí běžet POSLEDNÍ: čte,
# které grafy generátory před ním v tomto běhu dokončily; i ono je za `||`.
CMD ["sh", "-c", "python scripts/run_all.py && python scripts/generate_json.py && python scripts/detect_anomalies.py && (python scripts/detect_anomalies.py --zdroj tydenni || echo 'anomaly_signals_weekly: selhal, pokračuji bez něj') && python scripts/compute_mem.py && (python scripts/compute_nowcast.py || echo 'flu_nowcast: selhal, pokračuji bez něj') && (python scripts/compute_resp.py || echo 'compute_resp: selhal, pokračuji bez něj') && (python scripts/compute_genomics.py || echo 'compute_genomics: selhal, pokračuji bez něj') && (python scripts/compute_isin.py || echo 'compute_isin: selhal, pokračuji bez něj') && (python scripts/compute_esurv.py || echo 'compute_esurv: selhal, pokračuji bez něj') && (python scripts/last_checked.py || echo 'last_checked: selhal, pokračuji bez něj')"]

"""
Pipeline placeholder — future data ingestion scripts go here.

Planned modules:
  scraper/     - fetch raw data from source APIs (ECDC, GISAID, SZÚ, ...)
  transform/   - clean and normalise data
  db/          - write to PostgreSQL (dashboard_data table)
  generate/    - render Hugo Markdown from DB snapshots
"""

import os
import time

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "pathogens")
DB_USER = os.getenv("DB_USER", "portal")

print(f"[pipeline] placeholder started — will connect to {DB_USER}@{DB_HOST}/{DB_NAME}")
print("[pipeline] no pipeline logic implemented yet — exiting")
  
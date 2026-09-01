"""
ETL: stažená CSV → PostgreSQL (tabulky `observation`, `population`).

Proč: `generate_json.py` dnes čte CSV přímo, což stačí na vykreslení grafu, ale ne na
analytiku (baseline, detekce anomálií, incidence napříč zdroji). Normalizovaná tabulka
`observation` dává jeden tvar pro všechny zdroje, takže dotaz "kolik případů čeho, kde,
kdy" nepotřebuje vědět, ze kterého scraperu data pocházejí.

Idempotence: zápis je UPSERT přes UNIQUE klíč včetně `snapshot_date`. Opakované
spuštění téhož dne přepíše tytéž řádky; spuštění jiný den přidá novou verzi téhož
pozorování — a právě z toho vzniká reporting triangle pro nowcasting.

Použití:
    python scripts/load_to_db.py            # vše, snapshot_date = dnes
    python scripts/load_to_db.py --dry-run  # jen spočítá, nic nezapíše

Proměnné prostředí:
    DATA_DIR    kde jsou stažená CSV (default: ./data)
    DB_HOST     default localhost      POSTGRES_DB       default pathogens
    DB_PORT     default 5432           POSTGRES_USER     default portal
                                       POSTGRES_PASSWORD default portal_dev
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))


def dsn() -> str:
    return (
        f"host={os.environ.get('DB_HOST', 'localhost')} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('POSTGRES_DB', 'pathogens')} "
        f"user={os.environ.get('POSTGRES_USER', 'portal')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', 'portal_dev')}"
    )


# ── population ───────────────────────────────────────────────────────────────

SEX_MAP = {"Celkem": "total", "Muži": "male", "Ženy": "female"}


def load_population(conn, dry_run: bool = False) -> int:
    path = DATA_DIR / "csu" / "population.csv"
    if not path.exists():
        print(f"  [population] {path} neexistuje — přeskakuji")
        return 0

    df = pd.read_csv(path)
    rows = [
        (r.kraj_kod, r.kraj_nazev, "total", SEX_MAP.get(r.pohlavi, r.pohlavi), int(r.rok), int(r.pocet))
        for r in df.itertuples()
    ]
    if dry_run:
        print(f"  [population] {len(rows):,} řádků (dry-run)")
        return len(rows)

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO population (region_code, region_name, age_group, sex, year, value)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (region_code, age_group, sex, year)
            DO UPDATE SET value = EXCLUDED.value, region_name = EXCLUDED.region_name
            """,
            rows,
        )
    print(f"  [population] {len(rows):,} řádků")
    return len(rows)


# ── observation: ISIN ────────────────────────────────────────────────────────

def load_isin(conn, snapshot: date, dry_run: bool = False) -> int:
    path = DATA_DIR / "isin" / "isin_infekcni_nemoci.csv"
    if not path.exists():
        print(f"  [isin] {path} neexistuje — přeskakuji")
        return 0

    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()

    # Měsíční agregace: kraj × věk × diagnóza. Pohlaví ISIN má, ale ne u všech
    # řádků konzistentně — agregujeme přes něj, ať nevznikají poloprázdné skupiny.
    grouped = (df.groupby(["rok", "mesic", "kraj_kod", "vek_nazev", "diagnoza_nazev"],
                          dropna=False)["pocet_pripadu"]
                 .sum().reset_index())

    rows = []
    for r in grouped.itertuples():
        start = date(int(r.rok), int(r.mesic), 1)
        end = (date(int(r.rok) + 1, 1, 1) if int(r.mesic) == 12
               else date(int(r.rok), int(r.mesic) + 1, 1))
        rows.append((
            "isin", None, str(r.diagnoza_nazev),
            (str(r.kraj_kod).strip() if pd.notna(r.kraj_kod) else None),
            (str(r.vek_nazev) if pd.notna(r.vek_nazev) else None),
            None, start, end, "cases", int(r.pocet_pripadu), snapshot,
        ))

    if dry_run:
        print(f"  [isin] {len(rows):,} řádků (dry-run)")
        return len(rows)

    _upsert_observations(conn, rows)
    print(f"  [isin] {len(rows):,} řádků")
    return len(rows)


def _upsert_observations(conn, rows: list) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO observation
                (source_id, diagnosis_code, diagnosis_name, region_code, age_group,
                 sex, period_start, period_end, metric, value, snapshot_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, diagnosis_name, region_code, age_group, sex,
                         period_start, metric, snapshot_date)
            DO UPDATE SET value = EXCLUDED.value, ingested_at = NOW()
            """,
            rows,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="jen spočítat, nic nezapisovat")
    ap.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD (default: dnes)")
    args = ap.parse_args()

    snap = date.fromisoformat(args.snapshot_date) if args.snapshot_date else date.today()
    print(f"Načítám do Postgresu (snapshot_date={snap})…")

    if args.dry_run:
        load_population(None, dry_run=True)
        load_isin(None, snap, dry_run=True)
        return 0

    with psycopg.connect(dsn()) as conn:
        load_population(conn)
        load_isin(conn, snap)
        conn.commit()
    print("Hotovo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

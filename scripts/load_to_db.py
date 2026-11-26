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
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
SCHEMA_FILE = ROOT / "db" / "init.sql"


def ensure_schema(conn) -> None:
    """
    Zajistí, že tabulky existují — aplikací `db/init.sql`.

    Proč to loader dělá sám: Postgres spustí skripty z `docker-entrypoint-initdb.d`
    jen při první inicializaci prázdného datového svazku. Na databázi, která už
    jednou běžela, se `init.sql` znovu nespustí, takže tabulky přidané později
    (`observation`, `population`) by tam nikdy nevznikly a zápis by spadl na
    "relation does not exist".

    Schéma se schválně neopisuje do Pythonu — `db/init.sql` zůstává jediným
    zdrojem pravdy a je celý idempotentní (CREATE TABLE IF NOT EXISTS,
    ON CONFLICT DO NOTHING), takže opakované spuštění nic nerozbije.
    """
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"Chybí schéma {SCHEMA_FILE}")
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


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


# ── observation: SZÚ týdenní respirační data ─────────────────────────────────

# Krajská PDF hlásí po virologických laboratořích; Praha a Střední Čechy mají
# společné laboratoře a nejdou rozdělit — kombinovaný kód je menší zlo než
# vylhané rozpočítání.
SZU_REGION_NUTS = {
    "Praha + Středočeský kraj": "CZ010+CZ020",
    "Jihomoravský kraj": "CZ064", "Jihočeský kraj": "CZ031",
    "Karlovarský kraj": "CZ041", "Královéhradecký kraj": "CZ052",
    "Liberecký kraj": "CZ051", "Moravskoslezský kraj": "CZ080",
    "Olomoucký kraj": "CZ071", "Pardubický kraj": "CZ053",
    "Plzeňský kraj": "CZ032", "Ústecký kraj": "CZ042", "Vysočina": "CZ063",
}


def _week_span(rok: int, tyden: int):
    try:
        start = date.fromisocalendar(int(rok), int(tyden), 1)
    except ValueError:
        return None, None
    return start, start + __import__("datetime").timedelta(days=7)


def load_szu_weekly(conn, snapshot: date, dry_run: bool = False) -> int:
    rows = []
    viry_path = DATA_DIR / "szu" / "szu_weekly_viry.csv"
    if viry_path.exists():
        for r in pd.read_csv(viry_path).itertuples():
            start, end = _week_span(r.rok, r.tyden)
            if start is None:
                continue
            rows.append(("szu", None, str(r.virus), None, None, None,
                         start, end, "lab_detections", int(r.pocet), snapshot))

    kraje_path = DATA_DIR / "szu" / "szu_weekly_kraje.csv"
    if kraje_path.exists():
        for r in pd.read_csv(kraje_path).itertuples():
            start, end = _week_span(r.rok, r.tyden)
            code = SZU_REGION_NUTS.get(str(r.kraj))
            if start is None or code is None:
                continue
            rows.append(("szu", None, "Respirační viry (souhrn)", code, None, None,
                         start, end, "lab_detections", int(r.pozitivni), snapshot))
            rows.append(("szu", None, "Respirační viry (souhrn)", code, None, None,
                         start, end, "tests", int(r.vysetreno), snapshot))

    if not rows:
        print("  [szu_weekly] žádné soubory — přeskakuji")
        return 0
    if dry_run:
        print(f"  [szu_weekly] {len(rows):,} řádků (dry-run)")
        return len(rows)
    _upsert_observations(conn, rows)
    print(f"  [szu_weekly] {len(rows):,} řádků")
    return len(rows)


# ── observation: registry ÚZIS mimo ISIN (pohlavní nemoci, tuberkulóza) ─────

def load_uzis_registries(conn, snapshot: date, dry_run: bool = False) -> int:
    """
    Roční řady. Kraj u RPN = prvních pět znaků kódu okresu bydliště; u RTBC kraj
    dispenzarizace, jak ho dává zdroj. Rodná země u TBC se do `observation` nevejde
    (tabulka pro ni nemá dimenzi) — zůstává jen v CSV a v grafech.
    """
    rows = []
    sti = DATA_DIR / "uzis" / "uzis_pohlavni_nemoci.csv"
    if sti.exists():
        df = pd.read_csv(sti, encoding="utf-8-sig")
        df["kraj"] = df["okres_kod"].str[:5]
        grouped = (df.groupby(["rok", "kraj", "vek_nazev", "pohlavi", "diagnoza_kod", "diagnoza_nazev"],
                              dropna=False)["pocet_pripadu"].sum().reset_index())
        for r in grouped.itertuples():
            rows.append(("uzis_rpn", str(r.diagnoza_kod), str(r.diagnoza_nazev), str(r.kraj),
                         str(r.vek_nazev), {"M": "male", "Z": "female"}.get(r.pohlavi),
                         date(int(r.rok), 1, 1), date(int(r.rok) + 1, 1, 1), "cases",
                         int(r.pocet_pripadu), snapshot))

    tbc = DATA_DIR / "uzis" / "uzis_tuberkuloza.csv"
    if tbc.exists():
        df = pd.read_csv(tbc, encoding="utf-8-sig")
        grouped = (df.groupby(["rok_incidence", "kraj_kod", "vek_nazev"], dropna=False)["pripady"]
                     .sum().reset_index())
        for r in grouped.itertuples():
            rows.append(("uzis_rtbc", "A15-A19", "Tuberkulóza", str(r.kraj_kod), str(r.vek_nazev), None,
                         date(int(r.rok_incidence), 1, 1), date(int(r.rok_incidence) + 1, 1, 1),
                         "cases", int(r.pripady), snapshot))

    if not rows:
        print("  [uzis_registries] žádné soubory — přeskakuji")
        return 0
    if dry_run:
        print(f"  [uzis_registries] {len(rows):,} řádků (dry-run)")
        return len(rows)
    _upsert_observations(conn, rows)
    print(f"  [uzis_registries] {len(rows):,} řádků")
    return len(rows)


# ── observation: WHO FluNet/FluID + ECDC ERVISS ──────────────────────────────

# FluNet hlásí za týž týden dva nezávislé systémy (sentinel ~50 vzorků,
# nonsentinel ~2 000). Unikátní klíč `observation` pro ně nemá dimenzi, a sečíst
# je by smíchalo dvě různé populace — proto každý dostane vlastní source_id.
FLUNET_SOURCE = {
    "SENTINEL": "who_flunet_sentinel",
    "NONSENTINEL": "who_flunet_nonsentinel",
    "NOTDEFINED": "who_flunet",
}
FLUNET_SERIES = {"inf_celkem": "Influenza", "inf_a": "Influenza A",
                 "inf_b": "Influenza B", "rsv": "RSV"}


def _age(value) -> str | None:
    """'All'/'total' = nerozlišeno podle věku → NULL (konvence tabulky)."""
    return None if str(value) in ("All", "total") else str(value)


def load_who_flu(conn, snapshot: date, dry_run: bool = False) -> int:
    rows = []
    flunet_path = DATA_DIR / "who" / "who_flunet_cz.csv"
    if flunet_path.exists():
        for r in pd.read_csv(flunet_path).itertuples():
            start, end = _week_span(r.rok, r.tyden)
            source = FLUNET_SOURCE.get(str(r.zdroj))
            if start is None or source is None:
                continue
            for col, name in FLUNET_SERIES.items():
                val = getattr(r, col)
                if pd.notna(val):
                    rows.append((source, None, name, None, None, None,
                                 start, end, "lab_detections", int(val), snapshot))
            # Jmenovatel FluNet jsou vzorky vyšetřené na chřipku — k RSV nepatří.
            if pd.notna(r.vysetreno):
                rows.append((source, None, "Influenza", None, None, None,
                             start, end, "tests", int(r.vysetreno), snapshot))

    fluid_path = DATA_DIR / "who" / "who_fluid_cz.csv"
    if fluid_path.exists():
        for r in pd.read_csv(fluid_path).itertuples():
            start, end = _week_span(r.rok, r.tyden)
            if start is None:
                continue
            for name, cases, pop in (("ILI", r.ili_pripady, r.ili_populace),
                                     ("ARI", r.ari_pripady, r.ari_populace)):
                # Populace 0 = věková skupina, kterou ČR v tom období nehlásila.
                if pd.isna(cases) or pd.isna(pop) or int(pop) == 0:
                    continue
                rows.append(("who_fluid", None, name, None, _age(r.vek), None,
                             start, end, "cases", int(cases), snapshot))
                rows.append(("who_fluid", None, name, None, _age(r.vek), None,
                             start, end, "population_covered", int(pop), snapshot))

    if not rows:
        print("  [who_flu] žádné soubory — přeskakuji")
        return 0
    if dry_run:
        print(f"  [who_flu] {len(rows):,} řádků (dry-run)")
        return len(rows)
    _upsert_observations(conn, rows)
    print(f"  [who_flu] {len(rows):,} řádků")
    return len(rows)


def load_erviss(conn, snapshot: date, dry_run: bool = False) -> int:
    # Jen míry ILI/ARI. Laboratorní část ERVISS (erviss_nonsentinel_cz.csv) jsou
    # tatáž hlášení jako FluNet nonsentinel — do DB by šla dvakrát.
    path = DATA_DIR / "ecdc" / "erviss_ili_ari_cz.csv"
    if not path.exists():
        print(f"  [erviss] {path} neexistuje — přeskakuji")
        return 0

    rows = []
    for r in pd.read_csv(path).itertuples():
        start, end = _week_span(r.tyden_iso[:4], r.tyden_iso[6:])
        if start is None or pd.isna(r.mira_na_100k):
            continue
        rows.append(("ecdc_erviss", None, str(r.ukazatel), None, _age(r.vek), None,
                     start, end, "rate_per_100k", float(r.mira_na_100k), snapshot))

    if dry_run:
        print(f"  [erviss] {len(rows):,} řádků (dry-run)")
        return len(rows)
    _upsert_observations(conn, rows)
    print(f"  [erviss] {len(rows):,} řádků")
    return len(rows)


# ── denní e-surveillance (ÚZIS) ──────────────────────────────────────────────

ESURV_SOURCE = "uzis_esurv"
ESURV_NAMES = {"chripka": "Chřipka", "covid19": "COVID-19", "rsv": "RSV", "ari_ili": "ARI/ILI"}
# sloupec v esurv_denni.csv → metrika v observation
ESURV_METRICS = {"pripady": "cases", "hosp_nove": "hospitalizations", "hosp_aktualne": "hospitalized",
                 "jip_nove": "icu_admissions", "umrti_hosp": "deaths"}


def esurveillance_rows(snapshot: date | None = None) -> tuple[list, date | None]:
    """
    Řádky pro `observation` z denní e-surveillance a datum snímku, pod kterým jdou.

    Snímek = datum denní složky u zdroje, ne den nahrání: opakované nahrání téže
    složky přepíše tytéž řádky a nová verze čísel vznikne, až ÚZIS vydá další den.

    Co se nahrává: celá ČR po dnech celkem i podle věku, kraje jen celkem (věk × kraj
    × den by dal přes 400 tisíc řádků na snímek, většinou nul). U rozpadů podle věku
    a kraje se nulové hodnoty nezapisují — chybějící řádek uvnitř rozsahu řady je
    nula; celostátní součty jsou úplné včetně nul.
    """
    import json

    base = DATA_DIR / "uzis_esurv"
    state_path = base / "esurv_stav.json"
    if not (base / "esurv_denni.csv").exists() or not state_path.exists():
        return [], None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    snap = snapshot or date.fromisoformat(state["slozka"])

    rows = []
    df = pd.read_csv(base / "esurv_denni.csv", dtype={"vek": str})
    df = df[(df["kraj_kod"] == "CZ") | (df["vek"] == "celkem")]
    for column, metric in ESURV_METRICS.items():
        sub = df[["datum", "nemoc", "kraj_kod", "vek", column]].dropna(subset=[column])
        national_total = (sub["kraj_kod"] == "CZ") & (sub["vek"] == "celkem")
        sub = sub[national_total | (sub[column] != 0)]
        for r in sub.itertuples(index=False):
            start = date.fromisoformat(r.datum)
            rows.append((ESURV_SOURCE, None, ESURV_NAMES[r.nemoc], None if r.kraj_kod == "CZ" else r.kraj_kod,
                         None if r.vek == "celkem" else r.vek, None, start, start + timedelta(days=1),
                         metric, float(getattr(r, column)), snap))

    vax_path = base / "esurv_ockovani.csv"
    if vax_path.exists():
        vax = pd.read_csv(vax_path, dtype={"kategorie": str})
        vax = vax[(vax["kategorie"] != "neznamy") & (vax["davky"] != 0)]
        # Součet za ČR je v obou rozpadech; bere se jednou, z rozpadu podle věku.
        vax = vax[~((vax["rozmer"] == "kraj") & (vax["kategorie"] == "CZ"))]
        for r in vax.itertuples(index=False):
            start = date.fromisoformat(r.datum)
            region = r.kategorie if r.rozmer == "kraj" else None
            age = None if r.rozmer == "kraj" or r.kategorie == "celkem" else r.kategorie
            rows.append((ESURV_SOURCE, None, ESURV_NAMES[r.nemoc], region, age, None, start,
                         start + timedelta(days=1), "vaccine_doses", float(r.davky), snap))
    return rows, snap


def load_esurveillance(conn, snapshot: date | None = None, dry_run: bool = False) -> int:
    rows, snap = esurveillance_rows(snapshot)
    if not rows:
        print("  [esurveillance] denní e-surveillance není stažená — přeskakuji")
        return 0
    if dry_run:
        print(f"  [esurveillance] {len(rows):,} řádků, snímek {snap} (dry-run)")
        return len(rows)
    _upsert_observations(conn, rows)
    # Zdroj vydává novou verzi celé řady každý den. Držet všechny by bylo přes sto
    # tisíc řádků denně kvůli opravám posledních dnů; drží se proto poslední snímek
    # a z historie pondělní (stav k neděli) — týdenní krok na dopočet zpoždění stačí.
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM observation
             WHERE source_id = %s
               AND snapshot_date <> (SELECT max(snapshot_date) FROM observation WHERE source_id = %s)
               AND EXTRACT(ISODOW FROM snapshot_date) <> 1
            """,
            (ESURV_SOURCE, ESURV_SOURCE),
        )
        pruned = cur.rowcount
    print(f"  [esurveillance] {len(rows):,} řádků, snímek {snap}"
          + (f"; smazáno {pruned:,} řádků starších denních snímků" if pruned else ""))
    return len(rows)


# ── registr patogenů ─────────────────────────────────────────────────────────

def load_pathogen_registry(conn, dry_run: bool = False) -> bool:
    """
    Registr patogenů (curated/pathogens.yaml) do tabulek `pathogens`, `pathogen_*`.

    Běží ve vlastním savepointu a chybu jen ohlásí: překlep v registru nesmí
    zahodit načtená pozorování. Vrací False, když se registr načíst nepodařilo —
    main() pak skončí kódem 1, aby to nezapadlo.
    """
    import load_pathogens
    from pathogen_registry import RegistryError

    try:
        if dry_run:
            load_pathogens.run(dry_run=True)
        else:
            with conn.transaction():
                load_pathogens.run(conn)
    except (RegistryError, psycopg.Error) as e:
        print(f"  [pathogens] selhal, pokračuji bez něj: {e}", file=sys.stderr)
        return False
    return True


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

    # U denní e-surveillance je snímkem datum složky u zdroje; --snapshot-date ho přebije.
    explicit_snap = date.fromisoformat(args.snapshot_date) if args.snapshot_date else None
    snap = explicit_snap or date.today()
    print(f"Načítám do Postgresu (snapshot_date={snap})…")

    if args.dry_run:
        load_pathogen_registry(None, dry_run=True)
        load_population(None, dry_run=True)
        load_isin(None, snap, dry_run=True)
        load_szu_weekly(None, snap, dry_run=True)
        load_uzis_registries(None, snap, dry_run=True)
        load_who_flu(None, snap, dry_run=True)
        load_erviss(None, snap, dry_run=True)
        load_esurveillance(None, explicit_snap, dry_run=True)
        return 0

    with psycopg.connect(dsn()) as conn:
        ensure_schema(conn)
        registry_ok = load_pathogen_registry(conn)
        load_population(conn)
        load_isin(conn, snap)
        load_szu_weekly(conn, snap)
        load_uzis_registries(conn, snap)
        load_who_flu(conn, snap)
        load_erviss(conn, snap)
        load_esurveillance(conn, explicit_snap)
        conn.commit()
    print("Hotovo.")
    return 0 if registry_ok else 1


if __name__ == "__main__":
    sys.exit(main())

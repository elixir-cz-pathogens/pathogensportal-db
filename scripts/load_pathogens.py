"""
Registr patogenů → PostgreSQL (tabulky `pathogens`, `pathogen_*`, `diagnosis`, `dashboard`).

Proč: portál jmenuje týž patogen na několika místech pokaždé jinak a nikde
s identifikátorem. `curated/pathogens.yaml` to drží na jednom místě; tenhle skript
z něj staví databázi, ve které se dá ptát — pod jakým číslem je patogen v NCBI/ENA,
jak se jmenuje česky a anglicky, pod kterými kódy MKN-10 je v datech, na kterých
stránkách portálu a kdy se jeho data naposledy pohnula.

Databáze je kopie, registr originál. Běh je idempotentní a kopii dorovná přesně
na registr: nové záznamy přidá, změněné přepíše, vyřazené smaže. Záznam, který se
nezměnil, nechá být i s `updated_at` — to pak poctivě říká, kdy se patogen
v databázi naposledy změnil, ne kdy naposledy běžel loader.

Použití:
    python scripts/load_pathogens.py             # zkontroluje registr a zapíše
    python scripts/load_pathogens.py --dry-run   # jen zkontroluje a spočítá, bez databáze

Proměnné prostředí: stejné jako u load_to_db.py (DB_HOST, DB_PORT, POSTGRES_*),
navíc OUTPUT_DIR — odtud se čte datum dat stránek (default ./site/static/data/charts).
"""

import argparse
import os
import sys
from pathlib import Path

import pathogen_registry as registry

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))


def summary(reg: dict, voc: dict) -> dict:
    """Co se zapíše — počty řádků podle tabulek. Stejné pro --dry-run i ostrý běh."""
    pathogens = reg["pathogens"]
    return {
        "pathogens": len(pathogens),
        "pathogen_identifier": sum(len(registry.identifiers(p)) for p in pathogens),
        "pathogen_term": sum(len(registry.terms(p)) for p in pathogens),
        "pathogen_diagnosis": sum(len(registry.diagnoses(p)) for p in pathogens),
        "pathogen_dashboard": sum(len(p["dashboards"]) for p in pathogens),
        "identifier_scheme": len(reg["schemes"]),
        "dashboard": len(reg["dashboards"]),
        "diagnosis": len(voc),
    }


def _delete_missing(cur, table: str, column: str, keep: list) -> int:
    """Smaže řádky, jejichž klíč v registru už není. Jména tabulek jsou naše konstanty."""
    cur.execute(f"DELETE FROM {table} WHERE NOT ({column} = ANY(%s))", (list(keep),))
    return cur.rowcount


def load(conn, reg: dict, voc: dict, output_dir: Path = OUTPUT_DIR) -> dict:
    """
    Zapíše registr. Vrací {"inserted": [...], "updated": [...], "deleted": [...],
    "unchanged": n} podle id patogenů.

    Pořadí kopíruje cizí klíče: nejdřív číselníky (schémata, diagnózy, stránky),
    pak patogeny a jejich vazby, a mazání vyřazených číselníkových řádků až
    nakonec, kdy na ně už nic neukazuje.
    """
    result = {"inserted": [], "updated": [], "deleted": [], "unchanged": 0}
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO identifier_scheme (scheme, title, authority, homepage, uri_template)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (scheme) DO UPDATE SET title = EXCLUDED.title, authority = EXCLUDED.authority,
                homepage = EXCLUDED.homepage, uri_template = EXCLUDED.uri_template
            """,
            [(k, s["title"], s["authority"], s.get("homepage"), s["uri"]) for k, s in reg["schemes"].items()],
        )
        cur.executemany(
            """
            INSERT INTO diagnosis (code, name_cs, name_en, reader_cs, reader_en)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET name_cs = EXCLUDED.name_cs, name_en = EXCLUDED.name_en,
                reader_cs = EXCLUDED.reader_cs, reader_en = EXCLUDED.reader_en
            """,
            [(c, n["name_cs"], n["name_en"], n["reader_cs"], n["reader_en"]) for c, n in voc.items()],
        )
        # Datum dat stránky se čte při každém běhu znovu — mění se bez zásahu do registru.
        cur.executemany(
            """
            INSERT INTO dashboard (path, kind, chart, data_through, data_generated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (path) DO UPDATE SET kind = EXCLUDED.kind, chart = EXCLUDED.chart,
                data_through = EXCLUDED.data_through, data_generated_at = EXCLUDED.data_generated_at
            """,
            [(path, d["kind"], d.get("chart"), *registry.chart_freshness(output_dir, d.get("chart")))
             for path, d in reg["dashboards"].items()],
        )

        cur.execute("SELECT slug, id, record_hash FROM pathogens")
        existing = {slug: (pid, h) for slug, pid, h in cur.fetchall()}

        for p in reg["pathogens"]:
            slug, digest = p["id"], registry.record_hash(p)
            disease = p.get("disease") or {}
            values = (
                p["name"]["en"], p["name"]["cs"], p["name"]["en"], disease.get("cs"), disease.get("en"),
                p["scientific_name"], p["category"], p["taxon"], p["rank"], p.get("taxon_note"),
                p.get("species"), p.get("family"), p.get("parent"), p["verified"], digest,
            )
            if slug not in existing:
                cur.execute(
                    """
                    INSERT INTO pathogens (slug, name, name_cs, name_en, disease_cs, disease_en,
                        scientific_name, category, taxon_id, taxon_rank, taxon_note, species, family,
                        parent_slug, verified_on, record_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (slug, *values),
                )
                pid = cur.fetchone()[0]
                result["inserted"].append(slug)
            elif existing[slug][1] != digest:
                pid = existing[slug][0]
                cur.execute(
                    """
                    UPDATE pathogens SET name = %s, name_cs = %s, name_en = %s, disease_cs = %s,
                        disease_en = %s, scientific_name = %s, category = %s, taxon_id = %s,
                        taxon_rank = %s, taxon_note = %s, species = %s, family = %s, parent_slug = %s,
                        verified_on = %s, record_hash = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (*values, pid),
                )
                for table in ("pathogen_identifier", "pathogen_term", "pathogen_diagnosis",
                              "pathogen_dashboard"):
                    cur.execute(f"DELETE FROM {table} WHERE pathogen_id = %s", (pid,))
                result["updated"].append(slug)
            else:
                result["unchanged"] += 1
                continue

            cur.executemany(
                "INSERT INTO pathogen_identifier (pathogen_id, scheme, value) VALUES (%s, %s, %s)",
                [(pid, *row) for row in registry.identifiers(p)],
            )
            cur.executemany(
                "INSERT INTO pathogen_term (pathogen_id, lang, kind, term, source) VALUES (%s, %s, %s, %s, %s)",
                [(pid, *row) for row in registry.terms(p)],
            )
            cur.executemany(
                "INSERT INTO pathogen_diagnosis (pathogen_id, code, shared) VALUES (%s, %s, %s)",
                [(pid, *row) for row in registry.diagnoses(p)],
            )
            cur.executemany(
                "INSERT INTO pathogen_dashboard (pathogen_id, dashboard_path) VALUES (%s, %s)",
                [(pid, path) for path in p["dashboards"]],
            )

        # Vyřazené záznamy: vazby patogenu odejdou přes ON DELETE CASCADE.
        gone = sorted(set(existing) - {p["id"] for p in reg["pathogens"]})
        if gone:
            cur.execute("DELETE FROM pathogens WHERE slug = ANY(%s)", (gone,))
        result["deleted"] = gone
        _delete_missing(cur, "dashboard", "path", list(reg["dashboards"]))
        _delete_missing(cur, "diagnosis", "code", list(voc))
        _delete_missing(cur, "identifier_scheme", "scheme", list(reg["schemes"]))
    return result


def report(result: dict) -> None:
    print(f"  [pathogens] nových {len(result['inserted'])}, změněných {len(result['updated'])}, "
          f"smazaných {len(result['deleted'])}, beze změny {result['unchanged']}")
    for key, label in (("updated", "změněno"), ("deleted", "smazáno")):
        if result[key]:
            print(f"  [pathogens] {label}: {', '.join(result[key])}")


def run(conn=None, dry_run: bool = False) -> dict:
    """Vstup pro load_to_db.py i pro samostatný běh. Schéma musí být založené předem."""
    reg = registry.load()
    voc = registry.vocabulary(reg)
    counts = summary(reg, voc)
    if dry_run:
        print("  [pathogens] " + ", ".join(f"{k} {v}" for k, v in counts.items()) + " (dry-run)")
        unnamed = sorted({c for p in reg["pathogens"] for c, _ in registry.diagnoses(p)
                          if not voc[c]["name_cs"]})
        if unnamed:
            print(f"  [pathogens] bez českého názvu MKN-10: {', '.join(unnamed)}")
        return counts
    result = load(conn, reg, voc)
    report(result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Registr patogenů → PostgreSQL")
    ap.add_argument("--dry-run", action="store_true", help="jen zkontrolovat a spočítat, nic nezapisovat")
    args = ap.parse_args()

    try:
        if args.dry_run:
            run(dry_run=True)
            return 0
        # Až tady: --dry-run a testy nesmí potřebovat ovladač databáze.
        import psycopg

        import load_to_db

        with psycopg.connect(load_to_db.dsn()) as conn:
            load_to_db.ensure_schema(conn)
            run(conn)
            conn.commit()
    except registry.RegistryError as e:
        print(e, file=sys.stderr)
        return 1
    print("Hotovo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

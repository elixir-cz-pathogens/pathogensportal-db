"""
ECDC ERVISS — týdenní snapshoty laboratorních dat, řádky za ČR.
Zdroj: github.com/EU-ECDC/Respiratory_viruses_weekly_data, složka data/snapshots/
       (ECDC tam každý pátek uloží celé CSV tak, jak vypadalo ten den; od 2023-11-24)

Proč vedle ecdc_erviss.py: ten bere jen poslední verzi a přepisuje ji. Pro nowcast
je potřeba vědět, jak se číslo za daný týden postupně doplňovalo — tedy všechny
verze (reporting triangle). Vlastní archiv raw/ to umí taky, ale začal se plnit
až v září 2026; ECDC má historii od listopadu 2023, tj. dvě celé sezóny.

Non-sentinel detekce v ERVISS jsou táž čísla, která SZÚ publikuje v týdenních PDF
(ověřeno 9. 10. 2026: chřipka A, B i RSV sedí týden po týdnu, rozdíly 0–3 případy).
Faktory doplnění odhadnuté tady proto platí i pro graf flu_weekly.

Výstup: $DATA_DIR/ecdc/erviss_snapshots/<YYYY-MM-DD>_nonSentinelTestsDetections.csv.gz
        — jen řádky za ČR, jeden soubor na snapshot. Publikovaný snapshot se nemění,
        takže co už je stažené, se znovu nestahuje.

Do archivu raw/ se soubory nepředávají: už samy jsou neměnná datovaná historie.
"""

import io
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

TREE_URL = ("https://api.github.com/repos/EU-ECDC/Respiratory_viruses_weekly_data"
            "/git/trees/main?recursive=1")
RAW_URL = "https://raw.githubusercontent.com/EU-ECDC/Respiratory_viruses_weekly_data/main/"
KIND = "nonSentinelTestsDetections"
SNAP_RE = re.compile(rf"^data/snapshots/(\d{{4}}-\d{{2}}-\d{{2}})_{KIND}\.csv$")
COUNTRY = "Czechia"
REQUIRED = {"countryname", "yearweek", "pathogen", "pathogentype", "pathogensubtype",
            "indicator", "age", "value"}

# Snapshot vychází jednou týdně. Dokud je poslední stažený mladší, GitHub API se
# neptáme vůbec — pipeline běží každou hodinu a anonymní API má limit 60 dotazů/h.
FRESH_DAYS = 6
# První naplnění je ~1 GB (129 souborů po ≤10 MB). Po dávkách, ať jeden běh
# pipeline netrvá desítky minut; zbytek doběhne v dalších hodinových bězích.
MAX_PER_RUN = 40


def local_snapshots(dest: Path) -> dict[str, Path]:
    return {p.name[:10]: p for p in dest.glob(f"*_{KIND}.csv.gz")}


def remote_snapshots() -> list[str]:
    resp = requests.get(TREE_URL, timeout=60)
    resp.raise_for_status()
    tree = resp.json()
    if tree.get("truncated"):
        raise ValueError("[erviss_snapshots] GitHub vrátil zkrácený strom — výpis není úplný")
    dates = sorted(m.group(1) for e in tree.get("tree", []) if (m := SNAP_RE.match(e["path"])))
    if not dates:
        raise ValueError("[erviss_snapshots] ve složce data/snapshots/ nejsou žádné snapshoty "
                         f"{KIND} — ECDC změnilo strukturu repozitáře")
    return dates


def fetch_cz(snapshot: str) -> pd.DataFrame:
    url = f"{RAW_URL}data/snapshots/{snapshot}_{KIND}.csv"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    df = pd.read_csv(io.BytesIO(resp.content))
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"[erviss_snapshots] {snapshot}: chybí sloupce {sorted(missing)}")
    # Prázdný výsledek se uloží taky (jen hlavička) — znamená „ČR v tom snapshotu
    # nebyla“, a bez souboru bychom ho stahovali při každém běhu znovu.
    return df.loc[df["countryname"] == COUNTRY, sorted(REQUIRED)]


def download(output_dir: Path, today: date | None = None) -> list[str]:
    dest = output_dir / "erviss_snapshots"
    dest.mkdir(parents=True, exist_ok=True)
    have = local_snapshots(dest)
    today = today or date.today()

    if have and date.fromisoformat(max(have)) > today - timedelta(days=FRESH_DAYS):
        print(f"  [erviss_snapshots] {len(have)} snapshotů, poslední {max(have)} — aktuální")
        return []

    todo = [d for d in remote_snapshots() if d not in have]
    for snap in todo[:MAX_PER_RUN]:
        fetch_cz(snap).to_csv(dest / f"{snap}_{KIND}.csv.gz", index=False, compression="gzip")
    left = max(len(todo) - MAX_PER_RUN, 0)
    print(f"  [erviss_snapshots] staženo {min(len(todo), MAX_PER_RUN)}, celkem "
          f"{len(have) + min(len(todo), MAX_PER_RUN)}" + (f", zbývá {left}" if left else ""))
    # Nic do archivu raw/ — viz docstring.
    return []


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parent.parent.parent
    download(Path(os.environ.get("DATA_DIR", str(root / "data"))) / "ecdc")

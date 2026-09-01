"""
Datované snapshoty stažených dat — neměnná historie toho, co kdy přišlo ze zdroje.

Proč: scrapery přepisují soubory na místě, takže se zpětně nedá zjistit, jak data
vypadala k danému dni. Bez toho nejde sestavit reporting triangle (jak se čísla za
daný týden postupně doplňují dodatečnými hlášeními), na kterém stojí nowcasting —
korekce reportovacího zpoždění. Posledních pár týdnů v surveillance datech vždycky
vypadá uměle nízko a bez téhle historie to nejde opravit.

Chování:
  - snapshot jde do $DATA_DIR/raw/<YYYY-MM-DD>/<cesta relativní k DATA_DIR>.gz
  - dedup podle sha256 zdrojového souboru: nezměněný obsah se neukládá znovu.
    Bez toho by ~65 MB CSV na běh dělalo ~24 GB/rok převážně identických kopií.
  - $DATA_DIR/raw/manifest.json drží poslední hash a datum snapshotu na soubor
"""

import gzip
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def snapshot(files: list, data_root: Path, on_date: date | None = None) -> dict:
    """
    Uloží gzipované kopie `files` do datovaného adresáře pod $DATA_DIR/raw/.
    Soubory se shodným hashem jako minule se přeskočí.

    Vrací {"new": [relativní cesty], "unchanged": [relativní cesty]}.
    """
    raw_root = data_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_root / "manifest.json"
    manifest = _load_manifest(manifest_path)

    day = (on_date or date.today()).isoformat()
    new, unchanged = [], []

    for f in files:
        src = Path(f)
        if not src.exists():
            continue

        try:
            rel = src.relative_to(data_root)
        except ValueError:
            # Soubor mimo DATA_DIR — ulož aspoň pod holým jménem, ať se neztratí.
            rel = Path(src.name)

        digest = _sha256(src)
        if manifest.get(str(rel), {}).get("sha256") == digest:
            unchanged.append(str(rel))
            continue

        dest = raw_root / day / rel.parent / (rel.name + ".gz")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fin, gzip.open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)

        manifest[str(rel)] = {"sha256": digest, "snapshot": day}
        new.append(str(rel))

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return {"new": new, "unchanged": unchanged}

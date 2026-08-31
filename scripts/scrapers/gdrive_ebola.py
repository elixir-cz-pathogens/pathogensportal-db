"""
Stahuje nejnovější ZIP z Google Drive složky (IMG AV ČR — ebola data).
Folder: https://drive.google.com/drive/folders/18fckkEwFKXRoo58y6BgzT5iGOjOAG_bz

Chování:
  - Vypíše soubory ve složce, najde ZIPy.
  - Pro každý soubor porovná Drive file id s tím, co má uložené v lokálním
    manifestu (.ebola_manifest.json) — ne jen podle jména. Drive folder
    listing neposkytuje velikost ani datum modifikace, ale pokud IMG AV ČR
    znovu nahraje ZIP se stejným názvem, dostane nové id, takže manifest
    odhalí změnu bez nutnosti stahovat a hashovat obsah znovu.
  - Stáhne jen soubory s novým/změněným id. To také chrání před rate
    limitem Google Drive při opakovaných bězích nad desítkami historických
    souborů — dřívější přístup (stáhnout vše a porovnat hash) se při běhu
    nad ~70 soubory reálně zasekl na "Cannot retrieve the public link ...
    have had many accesses".
  - Selhání jednoho souboru nezastaví zbytek dávky — zůstane chybět
    v manifestu a příští běh ho zkusí znovu.
  - Volitelně rozbalí ZIP (extract=True).
"""

import json
from pathlib import Path
import gdown


FOLDER_ID = "18fckkEwFKXRoo58y6BgzT5iGOjOAG_bz"


def _load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def download(out_dir: Path, extract: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / ".ebola_manifest.json"
    manifest = _load_manifest(manifest_path)

    print(f"  Načítám obsah Google Drive složky {FOLDER_ID} ...")
    # Zjistí seznam souborů bez stahování
    files = gdown.download_folder(
        id=FOLDER_ID,
        output=str(out_dir),
        quiet=True,
        use_cookies=False,
        skip_download=True,
    )

    if not files:
        print("  Složka je prázdná nebo nepřístupná.")
        return []

    # gdown.download_folder občas vrátí stejný název souboru vícekrát
    # (různá Drive id) — bereme první výskyt, jinak bychom pro stejný
    # soubor zbytečně dělali druhý pokus o stažení a plýtvali kvótou.
    seen_names: set[str] = set()
    unique_files = []
    for f in files:
        name = Path(f.path).name
        if name in seen_names:
            continue
        seen_names.add(name)
        unique_files.append(f)

    downloaded = []
    failures = []
    for f in unique_files:
        filename = Path(f.path).name
        if not filename.lower().endswith(".zip"):
            continue

        dest = out_dir / filename

        if manifest.get(filename) == f.id and dest.exists():
            downloaded.append(dest)
            continue

        if filename not in manifest and dest.exists():
            # Soubor už na disku je (z doby před zavedením manifestu) a
            # jeho id ještě nebylo zaznamenané — důvěřuj existujícímu
            # souboru napoprvé místo zbytečného re-downloadu, jen si
            # zapiš id pro příští porovnání.
            manifest[filename] = f.id
            _save_manifest(manifest_path, manifest)
            downloaded.append(dest)
            continue

        if filename in manifest and dest.exists():
            print(f"  [{filename}] obsah se změnil pod stejným názvem (nové Drive id), aktualizuji.")

        print(f"  Stahuji {filename} ...")
        try:
            gdown.download(id=f.id, output=str(dest), quiet=False)
        except Exception as e:
            print(f"  [{filename}] CHYBA stahování: {e}")
            failures.append(filename)
            continue

        manifest[filename] = f.id
        _save_manifest(manifest_path, manifest)
        print(f"  OK → {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")

        if extract:
            import zipfile
            extract_dir = out_dir / dest.stem
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(dest) as z:
                z.extractall(extract_dir)
            print(f"  Rozbaleno → {extract_dir}/")

        downloaded.append(dest)

    if failures:
        print(f"  Nepodařilo se stáhnout {len(failures)} soubor(ů): {', '.join(failures)}")
        print("  (chybí v manifestu, příští běh je zkusí znovu)")

    return downloaded


if __name__ == "__main__":
    import os

    data_dir = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
    result = download(data_dir / "ebola", extract=False)
    for p in result:
        print(p)

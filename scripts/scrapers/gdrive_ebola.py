"""
Stahuje nejnovější ZIP z Google Drive složky (IMG AV ČR — ebola data).
Folder: https://drive.google.com/drive/folders/18fckkEwFKXRoo58y6BgzT5iGOjOAG_bz

Chování:
  - Vypíše soubory ve složce, najde ZIPy.
  - Přeskočí ZIP pokud už stejný název lokálně existuje.
  - Stáhne nové/aktualizované ZIPy do out_dir.
  - Volitelně rozbalí ZIP (extract=True).
"""

from pathlib import Path
import gdown


FOLDER_ID = "18fckkEwFKXRoo58y6BgzT5iGOjOAG_bz"


def download(out_dir: Path, extract: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

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

    downloaded = []
    for f in files:
        filename = Path(f.path).name
        if not filename.lower().endswith(".zip"):
            continue

        dest = out_dir / filename
        if dest.exists():
            print(f"  [{filename}] již existuje, přeskakuji.")
            downloaded.append(dest)
            continue

        print(f"  Stahuji {filename} ...")
        gdown.download(id=f.id, output=str(dest), quiet=False)
        print(f"  OK → {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")

        if extract:
            import zipfile
            extract_dir = out_dir / dest.stem
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(dest) as z:
                z.extractall(extract_dir)
            print(f"  Rozbaleno → {extract_dir}/")

        downloaded.append(dest)

    return downloaded


if __name__ == "__main__":
    result = download(
        Path(__file__).resolve().parents[2] / "data" / "ebola",
        extract=False,
    )
    for p in result:
        print(p)

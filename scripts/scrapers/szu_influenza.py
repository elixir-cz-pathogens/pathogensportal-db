"""
SZÚ chřipka/ARI scraper — extrakce dat z PDF archivů
Zdroj: szu.gov.cz — ZIP archivy historických sezón (2009/10 – 2021/22)
       + lokální PDFs pro novější sezóny (data/pdfs/)

Formáty PDF tabulek:
  Starý (2012-2022): 54 sloupců, jeden KT per sloupec, virus v col1
  Nový A (2022-2024): multi-value buňky, virus v col1, poslední sl. = Kumulativně
  Nový B (2024-2025): kategorie+virus sloučeny v col0, poslední sl. = Kumulativně
"""

import io
import re
import zipfile
import requests
import pandas as pd
import pdfplumber
from pathlib import Path

BASE_URL = "https://szu.gov.cz/wp-content/uploads/2023/03"

SEASONS = [
    "2012_2013", "2013_2014", "2014_2015", "2015_2016",
    "2016_2017", "2017_2018", "2018_2019", "2019_2020",
    "2020_2021", "2021_2022",
]

VIRUS_NAMES = {
    "A":            "Influenza A",
    "A H1pdm":      "Influenza A/H1N1pdm",
    "A H1 pdm":     "Influenza A/H1N1pdm",
    "A H1":         "Influenza A/H1",
    "A H3":         "Influenza A/H3N2",
    "B":            "Influenza B",
    "HRSV":         "RSV",
    "RSV":          "RSV",
    "HAdV":         "Adenovirus",
    "AV":           "Adenovirus",
    "HPIV":         "Parainfluenza",
    "PIV":          "Parainfluenza",
    "HV":           "Herpesvirus",
    "MP":           "Mycoplasma pneumoniae",
    "HMPV":         "Metapneumovirus",
    "MPV":          "Metapneumovirus",
    "CoV":          "Coronavirus (sezónní)",
    "HRV":          "Rhinovirus",
    "HRV 2":        "Rhinovirus",
    "RV":           "Rhinovirus",
    "RHV":          "Rhinovirus",
    "CV":           "Coronavirus (sezónní)",
    "hBoV":         "Bocavirus",
    "BoV":          "Bocavirus",
    "EV":           "Enterovirus",
    "SM":           "Smíšená infekce",
    "SARS-CoV-2":   "SARS-CoV-2",
}

CATEGORIES = ("Detekce viru", "Sérologie", "Izolace")
SKIP_SUBSTRINGS = ("celkový", "negativní", "pozitivní")


def _pdf_sort_key(name: str) -> tuple[int, int]:
    """Parsuj (rok, tyden) z názvu PDF — formáty: 43tyden_2025 i 43_tyden_2025."""
    m = re.search(r"_(\d+)_?tyden_(\d{4})\.pdf", name, re.IGNORECASE)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return (0, 0)


def _resolve_virus(raw: str) -> str | None:
    """Normalizuje název viru. Vrátí None pro souhrnné řádky a artefakty."""
    raw = raw.strip()
    if not raw:
        return None
    low = raw.lower()
    if any(s in low for s in SKIP_SUBSTRINGS):
        return None
    if re.match(r"^[\d\s.]+$", raw):
        return None
    if raw in VIRUS_NAMES:
        return VIRUS_NAMES[raw]
    # Odstraň číselný suffix (PDF artefakt): "SM 1" → "SM", zachová "H1pdm"
    cleaned = re.sub(r"\s+\d+$", "", raw).strip()
    return VIRUS_NAMES.get(cleaned, cleaned)


def _parse_last_pdf(pdf_bytes: bytes, season: str) -> pd.DataFrame:
    """
    Parsuje poslední PDF sezóny.
    Zvládá tři formáty (starý one-KT-per-col a nové kumulativní formáty).
    """
    year1, year2 = (int(y) for y in season.split("_"))
    rows: list[dict] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or len(table) < 3:
                continue

            header = table[0]

            # Najdi kumulativní sloupec
            kumul_idx = None
            for i in range(len(header) - 1, max(len(header) - 5, -1), -1):
                if header[i] and "umulat" in str(header[i]).lower():
                    kumul_idx = i
                    break

            if kumul_idx is not None:
                # ── NOVÝ FORMÁT: použij kumulativní součet ──────────────────
                current_category = "Detekce viru"
                for row in table[1:]:
                    if not row or kumul_idx >= len(row):
                        continue

                    col0 = str(row[0]).strip() if row[0] else ""
                    col1 = str(row[1]).strip() if len(row) > 1 and row[1] else ""

                    virus_raw = None

                    # Vzor B: "Detekce viru A" — kategorie+virus v col0
                    for cat in CATEGORIES:
                        if col0.startswith(cat):
                            current_category = cat
                            suffix = col0[len(cat):].strip()
                            virus_raw = suffix if suffix else col1
                            break
                    else:
                        if col0 in CATEGORIES:
                            current_category = col0
                            virus_raw = col1
                        elif col0.startswith("Celkový"):
                            continue
                        else:
                            # Format A: col0="" col1="A H1pdm" (virus v col1)
                            # Format B sub-type: col0="HRSV" col1="1" (virus v col0, data v col1)
                            if col1 and not re.match(r"^[\d\s.]+$", col1):
                                virus_raw = col1  # col1 je název viru
                            elif col0 and not re.match(r"^[\d\s.]+$", col0):
                                virus_raw = col0  # col0 je název viru
                            else:
                                virus_raw = None

                    if not virus_raw:
                        continue

                    virus = _resolve_virus(virus_raw)
                    if not virus:
                        continue

                    val = str(row[kumul_idx]).strip() if row[kumul_idx] else ""
                    nums = re.findall(r"\d+", val)
                    if nums:
                        count = int(nums[0])
                        if count > 0:
                            rows.append({
                                "sezona":    season,
                                "rok":       year2,
                                "tyden_kt":  0,
                                "kategorie": current_category,
                                "virus":     virus,
                                "pocet":     count,
                            })

            else:
                # ── STARÝ FORMÁT: jeden KT per sloupec ──────────────────────
                week_cols = []
                for i, cell in enumerate(header):
                    s = str(cell).strip() if cell else ""
                    m = re.match(r"^(\d+)\.?$", s)
                    if m:
                        week_cols.append((i, int(m.group(1))))

                if not week_cols:
                    continue

                current_category = "Detekce viru"
                for row in table[1:]:
                    if not row:
                        continue
                    col0 = str(row[0]).strip() if row[0] else ""
                    col1 = str(row[1]).strip() if len(row) > 1 and row[1] else ""

                    if col0 in CATEGORIES:
                        current_category = col0
                        continue
                    if col0.startswith("Celkový"):
                        continue

                    virus_raw = col1 if col1 else col0
                    virus = _resolve_virus(virus_raw)
                    if not virus:
                        continue

                    for col_idx, week_num in week_cols:
                        if col_idx >= len(row):
                            continue
                        val = str(row[col_idx]).strip() if row[col_idx] else ""
                        if not val or not val.isdigit():
                            continue
                        count = int(val)
                        if count == 0:
                            continue
                        cal_year = year1 if week_num >= 37 else year2
                        rows.append({
                            "sezona":    season,
                            "rok":       cal_year,
                            "tyden_kt":  week_num,
                            "kategorie": current_category,
                            "virus":     virus,
                            "pocet":     count,
                        })

    return pd.DataFrame(rows)


def _season_filter(name: str, year1: int, year2: int) -> bool:
    """Vrátí True pokud PDF patří do sezóny (ne do příští)."""
    m = re.search(r"_(\d+)_?tyden_(\d{4})\.pdf", name, re.IGNORECASE)
    if not m:
        return True
    week, year = int(m.group(1)), int(m.group(2))
    return not (year == year2 and week >= 37)


def download(output_dir: Path, seasons: list[str] | None = None) -> list[str]:
    """Stáhne historické sezóny jako ZIP z webu SZÚ a parsuje PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    seasons = seasons or SEASONS
    downloaded = []

    for season in seasons:
        out_path = output_dir / f"szu_influenza_{season}.csv"
        if out_path.exists():
            print(f"  [szu_flu] {season} — uz existuje, preskakuji")
            downloaded.append(str(out_path))
            continue

        url = f"{BASE_URL}/{season}.zip"
        print(f"  [szu_flu] {season} — stahuji {url}")
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [szu_flu] {season} — chyba: {e}")
            continue

        year1, year2 = (int(y) for y in season.split("_"))
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            all_pdfs = [n for n in zf.namelist()
                        if "vysledky_vysetreni_vir" in n and n.endswith(".pdf")]
            if not all_pdfs:
                print(f"  [szu_flu] {season} — zadne PDF")
                continue

            vir_pdfs = [n for n in all_pdfs if _season_filter(n, year1, year2)] or all_pdfs
            last = max(vir_pdfs, key=_pdf_sort_key)
            print(f"  [szu_flu] {season} — {len(vir_pdfs)} PDFs, parsuju: {last.split('/')[-1]}")

            with zf.open(last) as f:
                pdf_bytes = f.read()

        try:
            df = _parse_last_pdf(pdf_bytes, season)
        except Exception as e:
            print(f"  [szu_flu] {season} — chyba parsovani: {e}")
            continue

        if df.empty:
            print(f"  [szu_flu] {season} — zadna data")
            continue

        df.to_csv(out_path, index=False, encoding="utf-8")
        top = df.groupby("virus")["pocet"].sum().sort_values(ascending=False).head(4)
        print(f"  [szu_flu] {season} — {len(df):,} zaznamu  top: {dict(top)}")
        downloaded.append(str(out_path))

    return downloaded


def download_local(pdf_base_dir: Path, output_dir: Path,
                   seasons: list[str] | None = None) -> list[str]:
    """Parsuje lokálně stažené PDF soubory (data/pdfs/) pro novější sezóny."""
    output_dir.mkdir(parents=True, exist_ok=True)
    local_seasons = seasons or [
        d.name for d in sorted(pdf_base_dir.iterdir())
        if d.is_dir() and re.match(r"\d{4}_\d{4}", d.name)
    ]
    downloaded = []

    for season in local_seasons:
        out_path = output_dir / f"szu_influenza_{season}.csv"
        if out_path.exists():
            print(f"  [szu_flu] {season} — uz existuje, preskakuji")
            downloaded.append(str(out_path))
            continue

        season_dir = pdf_base_dir / season
        vir_pdfs = [
            p for p in season_dir.iterdir()
            if "podle_typu_viru" in p.name and p.suffix == ".pdf"
        ] if season_dir.exists() else []

        if not vir_pdfs:
            print(f"  [szu_flu] {season} — zadne vir PDFs")
            continue

        year1, year2 = (int(y) for y in season.split("_"))
        filtered = [p for p in vir_pdfs if _season_filter(p.name, year1, year2)] or vir_pdfs
        last = max(filtered, key=lambda p: _pdf_sort_key(p.name))
        print(f"  [szu_flu] {season} — {len(filtered)} PDFs, parsuju: {last.name}")

        try:
            df = _parse_last_pdf(last.read_bytes(), season)
        except Exception as e:
            print(f"  [szu_flu] {season} — chyba parsovani: {e}")
            continue

        if df.empty:
            print(f"  [szu_flu] {season} — zadna data")
            continue

        df.to_csv(out_path, index=False, encoding="utf-8")
        top = df.groupby("virus")["pocet"].sum().sort_values(ascending=False).head(4)
        print(f"  [szu_flu] {season} — {len(df):,} zaznamu  top: {dict(top)}")
        downloaded.append(str(out_path))

    return downloaded


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    download(root / "data" / "szu")
    pdf_dir = root / "data" / "pdfs"
    if pdf_dir.exists():
        download_local(pdf_dir, root / "data" / "szu")

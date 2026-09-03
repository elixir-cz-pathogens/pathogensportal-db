"""
SZÚ — týdenní laboratorní data o respiračních virech z PDF běžící sezóny.

Proč vedle szu_influenza.py: sezónní scraper dává jen kumulativní souhrny
(týdenní řádky měla naposledy sezóna 2012/13). Indexová stránka SZÚ ale
hostuje PDF každého týdne běžící sezóny, a to dvojího druhu:

  laboratorni_vysetreni_podle_typu_viru_*  — JEDNO nejnovější PDF nese celou
      týdenní matici (virus × kalendářní týden) pro běžící I minulou sezónu,
      včetně kontrolního sloupce „Kumulativně“. Stačí tedy stáhnout poslední.
  laboratorni_vysetreni_podle_kraju_*      — každé PDF nese jeden týden po
      krajích (resp. virologických laboratořích). Stahují se všechna;
      publikované PDF se nemění, takže cache podle jména souboru stačí.

Extrakce ověřená dvěma nezávislými kontrolami: součet týdnů běžící sezóny
sedí na sloupec „Kumulativně“ (16/16 virů) a součet minulé sezóny na sezónní
CSV ze szu_influenza (14/16; dva rozdíly jsou zpětné korekce SZÚ).

Výstupy (do $DATA_DIR/szu):
  szu_weekly_viry.csv   — sezona, rok, tyden, virus, pocet
  szu_weekly_kraje.csv  — rok, tyden, kraj, pozitivni, vysetreno
"""

import io
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber
import requests

from .szu_influenza import CURRENT_SEASON_LISTING_URL, VIRUS_NAMES

PDF_RE = re.compile(
    r'href="(https://szu\.gov\.cz/wp-content/uploads/[^"]*'
    r'laboratorni_vysetreni_podle_(typu_viru|kraju)[^"]*\.pdf)"')
WEEK_RE = re.compile(r"(\d{1,2})_?_?tyden_(\d{4})")


def _week_key(url: str) -> tuple[int, int]:
    m = WEEK_RE.search(url)
    if not m:
        return (0, 0)
    week, year = int(m.group(1)), int(m.group(2))
    return (year, week)


# ── PDF podle typu viru: matice virus × týden ────────────────────────────────

def parse_viry_matrix(pdf_bytes: bytes) -> list[dict]:
    """
    Vrátí řádky {sezona, rok, tyden, virus, pocet}. Stránka nese dvě tabulky
    vedle sebe (minulá a běžící sezóna); dělí se podle ročních značek nad
    hlavičkou. Validuje se proti sloupci „Kumulativně“ běžící sezóny.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(keep_blank_chars=False, y_tolerance=1)
        chars = page.chars

    header_top = min(w["top"] for w in words
                     if w["text"].startswith("40") and w["top"] < 90)

    # Labely týdnů po znacích — hlavička má slité popisky i týden bez tečky.
    hchars = sorted((c for c in chars if abs(c["top"] - header_top) < 1.5),
                    key=lambda c: c["x0"])
    kumul_x = min((w["x0"] for w in words if w["text"].startswith("Kumulativ")),
                  default=10 ** 9)
    labels: list[tuple[int, float]] = []
    buf, x0, last_x1 = "", None, None

    def close(end_x):
        nonlocal buf, x0
        if buf:
            labels.append((int(buf), (x0 + end_x) / 2))
            buf = ""

    for c in hchars:
        if c["text"].isdigit():
            if buf and c["x0"] - last_x1 > 2.5:  # mezera = nový label bez tečky
                close(last_x1)
            if not buf:
                x0 = c["x0"]
            buf += c["text"]
            last_x1 = c["x1"]
        elif c["text"] == ".":
            close(c["x1"])
        else:
            close(last_x1 or 0)
    close(last_x1 or 0)
    # „Kumulativně od 40. KT“ v hlavičce by jinak vyrobilo parazitní týden 40
    labels = [l for l in labels if l[1] < kumul_x - 4]

    # Tabulky se dělí podle ročních značek (2024/2025…) těsně nad řádkem KT.
    year_marks = sorted(((w["x0"], int(w["text"])) for w in words
                         if re.fullmatch(r"20\d\d", w["text"])
                         and header_top - 6 < w["top"] < header_top),
                        key=lambda m: m[0])
    if not year_marks:
        raise ValueError("[szu_weekly] roční značky nad hlavičkou nenalezeny")
    bounds = [m[0] - 2 for m in year_marks[1:]] + [10 ** 9]
    tables: list[list] = [[] for _ in year_marks]
    for wk, xc in labels:
        for ti, b in enumerate(bounds):
            if xc < b:
                tables[ti].append((wk, xc))
                break

    regions = []
    for (mx, cap_year), tab in zip(year_marks, tables):
        seq, year, prev = [], cap_year, None
        for wk, xc in tab:
            if prev is not None and wk < prev:
                year += 1
            seq.append((wk, year, xc))
            prev = wk
        regions.append({"seq": seq, "start_year": cap_year})

    # Řádky: shluky slov podle svislé pozice, jen uvnitř výšky tabulky.
    rows, keys = defaultdict(list), []
    for w in sorted(words, key=lambda w: w["top"]):
        if w["top"] <= header_top + 1.5 or w["top"] > header_top + 62:
            continue
        for k in keys:
            if abs(k - w["top"]) < 1.6:
                rows[k].append(w)
                break
        else:
            keys.append(w["top"])
            rows[w["top"]].append(w)

    first_x = regions[0]["seq"][0][2]
    table_bounds = [regions[i + 1]["seq"][0][2] - 6 if i + 1 < len(regions) else 10 ** 9
                    for i in range(len(regions))]
    out, category, pending = [], None, ""
    failures = []
    for k in sorted(rows):
        line = sorted(rows[k], key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in line if w["x1"] < first_x - 6).strip()
        if label.startswith("Detekce viru"):
            category, label = "Detekce viru", label.replace("Detekce viru", "").strip()
        elif label.startswith(("Sérologie", "Izolace")):
            category, label = label.split()[0], ""
        if category != "Detekce viru":
            continue
        nums = [(int(w["text"]), (w["x0"] + w["x1"]) / 2) for w in line
                if w["x0"] >= first_x - 6 and w["text"].isdigit()]
        if not nums:
            if label:
                pending = label
            continue
        if not label:
            label, pending = pending, ""
        if not label or label.split()[0].rstrip(":").lower() in (
                "pozitivní", "negativní", "celkový", "celkem"):
            continue  # součtové řádky mají slité číslice napříč sloupci

        for ri, reg in enumerate(regions):
            lo = table_bounds[ri - 1] if ri else -1
            hi = table_bounds[ri]
            is_last = ri == len(regions) - 1
            x_last = reg["seq"][-1][2]
            vals, kumul = {}, None
            for v, xc in nums:
                if not (lo < xc < hi):
                    continue
                if is_last and xc > x_last + 10:
                    if kumul is None:
                        kumul = v
                    continue
                best = min(reg["seq"], key=lambda s: abs(s[2] - xc))
                if abs(best[2] - xc) < 9:
                    key = (best[0], best[1])
                    vals[key] = vals.get(key, 0) + v
            if not vals:
                continue
            if is_last and kumul is not None and sum(vals.values()) != kumul:
                failures.append(f"{label}: součet {sum(vals.values())} ≠ kumulativně {kumul}")
            season = f"{reg['start_year']}_{reg['start_year'] + 1}"
            virus = VIRUS_NAMES.get(label, label)
            for (wk, yr), v in sorted(vals.items(), key=lambda i: (i[0][1], i[0][0])):
                out.append({"sezona": season, "rok": yr, "tyden": wk,
                            "virus": virus, "pocet": v})
    if failures:
        # Kontrolní součet je tu proto, aby tichá změna formátu neprošla.
        raise ValueError("[szu_weekly] validace matice selhala: " + "; ".join(failures))
    return out


# ── PDF podle krajů: jeden týden na soubor ───────────────────────────────────

def parse_kraje(pdf_bytes: bytes) -> tuple[int, int, list[dict]]:
    """Vrátí (rok, tyden, [{kraj, pozitivni, vysetreno}]). Laboratoře téhož
    kraje se sčítají; prázdná buňka = laboratoř ten týden nehlásila (0)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        table = page.extract_tables()[0]
        text = page.extract_text() or ""
    m = re.search(r"(\d{1,2})\.\s*KT\s*(\d{4})", text)
    if not m:
        raise ValueError("[szu_weekly] týden/rok v krajském PDF nenalezen")
    week, year = int(m.group(1)), int(m.group(2))

    agg: dict[str, list[int]] = {}
    region = None
    for row in table:
        cell0 = (row[0] or "").replace("\n", " ").strip()
        if cell0.startswith(("Územní", "Celkov")) or (row[2] or "") == "pozit.":
            continue
        if cell0:
            region = cell0
        if region is None:
            continue

        def num(c):
            c = (c or "").strip().replace(" ", "")
            return int(c) if c.isdigit() else 0

        agg.setdefault(region, [0, 0])
        agg[region][0] += num(row[2])
        agg[region][1] += num(row[3])
    rows = [{"kraj": k, "pozitivni": v[0], "vysetreno": v[1]} for k, v in agg.items()]
    return year, week, rows


# ── stahování ────────────────────────────────────────────────────────────────

def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_cache = output_dir / "pdf_kraje"
    pdf_cache.mkdir(exist_ok=True)

    resp = requests.get(CURRENT_SEASON_LISTING_URL, timeout=30)
    resp.raise_for_status()
    viry, kraje = [], []
    for url, kind in PDF_RE.findall(resp.text):
        (viry if kind == "typu_viru" else kraje).append(url)
    if not viry or not kraje:
        raise ValueError("[szu_weekly] indexová stránka nevrátila žádná týdenní PDF")

    # Matice virů: stačí nejnovější PDF, nese obě sezóny celé.
    latest = max(set(viry), key=_week_key)
    year, week = _week_key(latest)
    print(f"  [szu_weekly] matice virů: týden {week}/{year} ({latest.rsplit('/', 1)[-1]})")
    rows = parse_viry_matrix(requests.get(latest, timeout=60).content)
    import pandas as pd
    viry_path = output_dir / "szu_weekly_viry.csv"
    pd.DataFrame(rows).to_csv(viry_path, index=False, encoding="utf-8")
    seasons = sorted({r["sezona"] for r in rows})
    print(f"  [szu_weekly] {len(rows):,} týdenních hodnot, sezóny {seasons}")

    # Kraje: každý týden zvlášť; publikované PDF se nemění → cache podle jména.
    kraje_rows = []
    for url in sorted(set(kraje), key=_week_key):
        name = url.rsplit("/", 1)[-1]
        cached = pdf_cache / name
        if not cached.exists():
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            cached.write_bytes(r.content)
        try:
            year, week, rows = parse_kraje(cached.read_bytes())
        except Exception as e:
            print(f"  [szu_weekly] {name}: {e} — přeskakuji")
            continue
        for row in rows:
            kraje_rows.append({"rok": year, "tyden": week, **row})
    kraje_path = output_dir / "szu_weekly_kraje.csv"
    pd.DataFrame(kraje_rows).sort_values(["rok", "tyden", "kraj"]).to_csv(
        kraje_path, index=False, encoding="utf-8")
    weeks = {(r["rok"], r["tyden"]) for r in kraje_rows}
    print(f"  [szu_weekly] kraje: {len(weeks)} týdnů, {len(kraje_rows):,} řádků")
    return [str(viry_path), str(kraje_path)]


if __name__ == "__main__":
    import os
    root = Path(__file__).resolve().parent.parent.parent
    download(Path(os.environ.get("DATA_DIR", str(root / "data"))) / "szu")

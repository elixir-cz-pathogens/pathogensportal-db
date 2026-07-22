"""
Zpracuje nejnovější ebola ZIP z Google Drive (IMG AV ČR) a vygeneruje:
  1. site/static/data/charts/ebola_timeseries.json  — Chart.js časová řada
  2. site/content/en/dashboards/ebola-*.md          — Hugo stránky (jedna na každý HTML ze zipu)

Použití:
  python3 scripts/process_ebola.py

Proměnné prostředí (volitelné):
  PORTAL_DIR  — cesta k www repozitáři (default: ../../www)
  EBOLA_DIR   — cesta ke složce s ebola ZIP soubory (default: data/ebola)
"""

import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as html2md

ROOT        = Path(__file__).resolve().parent.parent
EBOLA_DIR   = Path(os.environ.get("EBOLA_DIR",  str(ROOT / "data" / "ebola")))
PORTAL_DIR  = Path(os.environ.get("PORTAL_DIR", str(ROOT.parent / "www")))
CHARTS_OUT  = PORTAL_DIR / "site" / "static" / "data" / "charts"
CONTENT_OUT = PORTAL_DIR / "site" / "content" / "en" / "dashboards"

# Mapování: HTML filename → (Hugo slug, český název stránky)
PAGE_MAP = {
    "index.html":              ("ebola-bundibugyo-2026",  "Aktuální stav"),
    "cesko.html":              ("ebola-cesko",             "Český kontext"),
    "virus.html":              ("ebola-virus",             "Ebolaviry"),
    "lecba-vakciny.html":      ("ebola-lecba-vakciny",    "Léčba a vakcíny"),
    "predchozi-epidemie.html": ("ebola-predchozi-epidemie", "Minulé epidemie"),
    "dezinformace.html":       ("ebola-dezinformace",     "Dezinformace"),
    "zdroje.html":             ("ebola-zdroje",           "Zdroje"),
    "graf-vyvoj.html":         ("ebola-graf-vyvoj",       "Graf vývoje"),
}

BASE_URL = "https://titan.img.cas.cz/ebola/"


# ── helpers ───────────────────────────────────────────────────────────────────

def find_latest_zip(directory: Path) -> Path:
    zips = sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        raise FileNotFoundError(f"Žádný ZIP nenalezen v {directory}")
    return zips[0]


def read_all_html(z: zipfile.ZipFile) -> dict[str, str]:
    """Vrátí dict {filename: html_text} pro všechny české HTML soubory ze zipu."""
    result = {}
    for name in z.namelist():
        if not name.endswith(".html") or "/en/" in name:
            continue
        fname = Path(name).name
        if fname in PAGE_MAP:
            with z.open(name) as f:
                result[fname] = f.read().decode("utf-8")
    return result


def parse_citation(cff_text: str) -> dict:
    result = {}
    for line in cff_text.splitlines():
        if line.startswith("version:"):
            result["version"] = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("date-released:"):
            result["date_released"] = line.split(":", 1)[1].strip().strip('"')
    return result


def parse_csv(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [r for r in reader if r.get("confirmed_cases_cumulative")]


def internal_href(html_filename: str) -> str:
    """Převede HTML filename na interní Hugo URL."""
    slug = PAGE_MAP.get(html_filename, (None,))[0]
    if slug:
        return f"/dashboards/{slug}/"
    return BASE_URL + html_filename


# ── HTML → Markdown ───────────────────────────────────────────────────────────

def fix_links(soup: BeautifulSoup) -> None:
    """Přepíše relativní href — interní stránky na Hugo URL, ostatní na absolutní."""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") or href.startswith("mailto") or href.startswith("#"):
            continue
        # Odděl fragment (#sekce) od souboru
        parts = href.split("#", 1)
        filename = parts[0]
        fragment = ("#" + parts[1]) if len(parts) > 1 else ""
        if filename in PAGE_MAP:
            a["href"] = internal_href(filename) + fragment
        else:
            a["href"] = BASE_URL + href


def build_nav_pills(current_file: str) -> str:
    """Sestaví Bootstrap nav-pills s interními Hugo linky."""
    links_html = ""
    for filename, (slug, label) in PAGE_MAP.items():
        href   = f"/dashboards/{slug}/"
        active = " active" if filename == current_file else ""
        links_html += (
            f'  <a href="{href}" '
            f'class="btn btn-sm btn-outline-secondary me-1 mb-1{active}">'
            f'{label}</a>\n'
        )
    return (
        '<div class="d-flex flex-wrap gap-1 mb-4" role="navigation" '
        'aria-label="Sekce situačního reportu">\n'
        + links_html
        + '</div>\n'
    )


def build_kam_dal_cards(soup: BeautifulSoup) -> str:
    """Sestaví Bootstrap kartičky pro sekci 'Kam dál' s interními linky."""
    h2_kam = soup.find(lambda t: t.name == "h2" and "Kam" in t.get_text())
    if not h2_kam:
        return ""
    grid_div = h2_kam.find_next_sibling("div")
    if not grid_div:
        return ""
    articles = grid_div.find_all("article", class_="card")
    if not articles:
        return ""

    cards_html = '<div class="row g-2 my-3">\n'
    for article in articles:
        a     = article.find("a")
        h3    = article.find("h3")
        p_tag = article.find("p")
        if not a:
            continue
        label    = (h3 or a).get_text(strip=True)
        raw_href = a.get("href", "")
        href     = internal_href(raw_href) if raw_href in PAGE_MAP else BASE_URL + raw_href
        desc     = p_tag.get_text(strip=True) if p_tag else ""

        cards_html += (
            f'  <div class="col-6 col-md-3">\n'
            f'    <a href="{href}" class="card h-100 text-decoration-none text-dark">\n'
            f'      <div class="card-body p-3">\n'
            f'        <div class="fw-semibold small">{label}</div>\n'
            f'        <div class="text-muted" style="font-size:0.75rem">{desc}</div>\n'
            f'      </div>\n'
            f'    </a>\n'
            f'  </div>\n'
        )
    cards_html += "</div>\n"
    return cards_html


def html_to_md(html_text: str, current_file: str) -> tuple[str, str]:
    """
    Převede HTML na (nav_pills_html, body_md).
    Sekce 'Kam dál' je odstraněna z body_md a vrácena jako Bootstrap kartičky
    přidané na konec stránky index.html.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    nav_pills    = build_nav_pills(current_file)
    kam_dal_html = build_kam_dal_cards(soup) if current_file == "index.html" else ""

    # Odstraň nepotřebné elementy
    for tag in soup.find_all(["header", "footer", "script", "style"]):
        tag.decompose()

    main = soup.find("main") or soup.find("body") or soup

    # Odstraň sekci "Kam dál" z těla
    h2_kam = main.find(lambda t: t.name == "h2" and "Kam" in t.get_text())
    if h2_kam:
        section = h2_kam.parent
        if section and section.name == "section":
            section.decompose()
        else:
            to_remove = [h2_kam] + list(h2_kam.next_siblings)
            for el in to_remove:
                if hasattr(el, "decompose"):
                    el.decompose()

    fix_links(main)

    md = html2md(
        str(main),
        heading_style="ATX",
        bullets="-",
        newline_style="backslash",
        strip=["img"],
    )
    md = re.sub(r"\n{3,}", "\n\n", md)

    if kam_dal_html:
        md = md.rstrip() + "\n\n## Kam dál\n\n" + kam_dal_html

    return nav_pills, md.strip()


# ── Chart JSON ────────────────────────────────────────────────────────────────

def generate_chart(rows: list[dict]) -> None:
    CHARTS_OUT.mkdir(parents=True, exist_ok=True)
    labels = [r["date"] for r in rows]
    cases  = [int(r["confirmed_cases_cumulative"]) for r in rows]
    deaths = [int(r["confirmed_deaths_cumulative"]) for r in rows]

    timeseries = {
        "labels": labels,
        "datasets": [
            {
                "label": "Potvrzené případy (kumulativní)",
                "data": cases,
                "backgroundColor": "rgba(13,110,253,0.15)",
                "borderColor": "rgb(13,110,253)",
                "borderWidth": 2,
                "fill": True,
                "tension": 0.3,
                "pointRadius": 2,
                "type": "line",
            },
            {
                "label": "Potvrzená úmrtí (kumulativní)",
                "data": deaths,
                "backgroundColor": "rgba(220,53,69,0.15)",
                "borderColor": "rgb(220,53,69)",
                "borderWidth": 2,
                "fill": True,
                "tension": 0.3,
                "pointRadius": 2,
                "type": "line",
            },
        ],
    }
    path = CHARTS_OUT / "ebola_timeseries.json"
    path.write_text(json.dumps(timeseries, ensure_ascii=False), encoding="utf-8")
    print(f"  [ebola_timeseries] → {path}")


# ── Hugo MD ───────────────────────────────────────────────────────────────────

CHART_BLOCK = """\

---

## Vývoj případů a úmrtí — DRC (kumulativní)

Data z kurátorované časové řady (CC BY 4.0, IMG AV ČR).

{{< chart id="ebolaTimeseries" src="/data/charts/ebola_timeseries.json" type="line" title="Ebola BDBV 2026 — kumulativní případy a úmrtí (DRC)" height="360" >}}

---

"""

FRONTMATTER_INDEX = """\
---
title: "Ebola — Bundibugyo virus (DRC/Uganda 2026)"
description: "Situační report vypuknutí Bundibugyo viru v DRC a Ugandě 2026. Zdroj: IMG AV ČR / UJEP. Verze {version}, ověřeno {date_released}."
image: "/images/dashboard-placeholder.svg"
highlight: true
tags: ["ebola", "epidemiologie", "IMG AV ČR", "Afrika"]
data_source: 'Jan Pačes & Michaela Liegertová — <a href="https://www.img.cas.cz" target="_blank">IMG AV ČR</a> · Licence CC BY 4.0'
update_freq: "Průběžně (aktivní vypuknutí) · Dataset verze {version} · {date_released}"
---

"""

FRONTMATTER_SUB = """\
---
title: "Ebola BDBV 2026 — {page_title}"
description: "Sekce situačního reportu: {page_title}. Zdroj: IMG AV ČR / UJEP."
tags: ["ebola", "epidemiologie", "IMG AV ČR", "Afrika"]
data_source: 'Jan Pačes & Michaela Liegertová — <a href="https://www.img.cas.cz" target="_blank">IMG AV ČR</a> · Licence CC BY 4.0'
build:
  list: never
  render: always
---

"""


def generate_page(filename: str, html_text: str, citation: dict) -> None:
    slug, page_title = PAGE_MAP[filename]
    nav_pills, body_md = html_to_md(html_text, filename)

    if filename == "index.html":
        fm = FRONTMATTER_INDEX.format(
            version=citation.get("version", "?"),
            date_released=citation.get("date_released", "?"),
        )
        # Vloží chart blok za druhý H2
        lines = body_md.split("\n")
        insert_at, h2_count = 0, 0
        for i, line in enumerate(lines):
            if line.startswith("## "):
                h2_count += 1
                if h2_count == 2:
                    insert_at = i
                    break
        if insert_at:
            lines.insert(insert_at, CHART_BLOCK)
            body_md = "\n".join(lines)
    else:
        fm = FRONTMATTER_SUB.format(page_title=page_title)

    md   = fm + nav_pills + body_md
    path = CONTENT_OUT / f"{slug}.md"
    CONTENT_OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    print(f"  [{slug}.md] → {path}")


# ── main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    zip_path = find_latest_zip(EBOLA_DIR)
    print(f"  ZIP: {zip_path.name}")

    with zipfile.ZipFile(zip_path) as z:
        html_files = read_all_html(z)
        csv_text   = read_zip_file_raw(z, "drc-bdbv-sitrep-timeseries.csv")
        cff_text   = read_zip_file_raw(z, "CITATION.cff")

    rows     = parse_csv(csv_text)
    citation = parse_citation(cff_text)
    last     = rows[-1]

    print(f"  Řádků v CSV: {len(rows)}, poslední datum: {last['date']}")
    print(f"  Verze datasetu: {citation.get('version')} ({citation.get('date_released')})")
    print(f"  HTML souborů k zpracování: {list(html_files.keys())}")

    generate_chart(rows)

    for filename, html_text in html_files.items():
        generate_page(filename, html_text, citation)

    print("  Hotovo.")


def read_zip_file_raw(z: zipfile.ZipFile, suffix: str) -> str:
    for name in z.namelist():
        if name.endswith(suffix):
            with z.open(name) as f:
                return f.read().decode("utf-8")
    raise KeyError(f"Soubor *{suffix} nenalezen v ZIP")


if __name__ == "__main__":
    run()

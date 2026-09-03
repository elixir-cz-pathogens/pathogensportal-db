"""
Zpracuje nejnovější ebola ZIP z Google Drive (IMG AV ČR) a vygeneruje:
  1. $OUTPUT_DIR/ebola_timeseries.json   — Chart.js časová řada
  2. $CONTENT_DIR/ebola-*.md              — Hugo stránky, česky (jedna na každý HTML ze zipu)

Použití:
  python3 scripts/process_ebola.py

Proměnné prostředí (volitelné):
  EBOLA_DIR    — cesta ke složce s ebola ZIP soubory (default: $DATA_DIR/ebola, stejná
                 proměnná jako run_all.py/gdrive_ebola.py)
  OUTPUT_DIR   — kam psát chart JSON, stejná proměnná jako generate_json.py
                 (default: site/static/data/charts v tomhle repu)
  CONTENT_DIR  — kam psát Hugo Markdown stránky (default: site/content/cs/dashboards
                 v tomhle repu)
"""

import csv
import io
import json
import os
import re
import zipfile
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as html2md

ROOT        = Path(__file__).resolve().parent.parent
_DATA_DIR   = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
EBOLA_DIR   = Path(os.environ.get("EBOLA_DIR", str(_DATA_DIR / "ebola")))
CHARTS_OUT  = Path(os.environ.get("OUTPUT_DIR",  str(ROOT / "site" / "static" / "data" / "charts")))
CONTENT_OUT = Path(os.environ.get("CONTENT_DIR", str(ROOT / "site" / "content" / "cs" / "dashboards")))

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


def _cell(value: str) -> str:
    value = str(value or "").strip()
    return value if value else "—"


def fill_data_table(main, rows: list[dict]) -> None:
    """
    Naplní „Tabulku hodnot“ daty.

    Zdrojové HTML má `<tbody>` prázdné — na původním webu ho doplňoval JavaScript
    z přiloženého CSV. Ten skript se sem nepřenáší (a CSV na portálu neleží), takže
    tabulka zůstávala prázdná a DataTables z motivu do ní psaly „No data available
    in table“. Řádky proto vypisujeme rovnou při generování: portál je statický,
    tabulka nemá důvod čekat na JavaScript.

    Datum zůstává ve tvaru ISO schválně — DataTables řadí sloupec jako řetězec,
    a „1. 9. 2026“ by se seřadilo před „30. 8. 2026“.
    """
    table = main.find(id="data-table")
    if table is None:
        return
    tbody = table.find("tbody")
    if tbody is None:
        return
    tbody.clear()

    soup = BeautifulSoup("", "html.parser")
    for r in rows:
        tr = soup.new_tag("tr")
        for key in ("date", "sitrep_number", "confirmed_cases_cumulative",
                    "confirmed_deaths_cumulative", "new_confirmed_cases_reported",
                    "daily_deaths_reported"):
            td = soup.new_tag("td")
            td.string = _cell(r.get(key))
            tr.append(td)

        td = soup.new_tag("td")
        url = str(r.get("source_url") or "").strip()
        if url:
            a = soup.new_tag("a", href=url)
            # Popiskem je doména, ne „odkaz“ — v seznamu odkazů (čtečka obrazovky)
            # tak jde poznat, kdo hodnotu hlásil.
            a.string = re.sub(r"^www\.", "", url.split("/")[2]) if "//" in url else url
            td.append(a)
        else:
            td.string = "—"
        tr.append(td)
        tbody.append(tr)


def html_to_md(html_text: str, current_file: str,
               rows: list[dict] | None = None) -> tuple[str, str]:
    """
    Převede HTML na (nav_pills_html, body_md).
    Sekce 'Kam dál' je odstraněna z body_md a vrácena jako Bootstrap kartičky
    přidané na konec stránky index.html.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    nav_pills    = build_nav_pills(current_file)
    kam_dal_html = build_kam_dal_cards(soup) if current_file == "index.html" else ""

    # Odstraň nepotřebné elementy. `noscript` je mezi nimi proto, že markdownify
    # jeho obsah rozbalí do běžného textu: poznámka určená čtenářům s vypnutým
    # JavaScriptem se pak zobrazovala všem — a od chvíle, kdy se tabulka hodnot
    # generuje staticky, navíc tvrdila něco, co není pravda.
    for tag in soup.find_all(["header", "footer", "script", "style", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("body") or soup

    if rows:
        fill_data_table(main, rows)

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

def _series(label: str, data: list, color: str, fill: bool = True, kind: str = "line") -> dict:
    return {
        "label": label, "data": data,
        "backgroundColor": f"rgba({color},0.15)", "borderColor": f"rgb({color})",
        "borderWidth": 2, "fill": fill, "tension": 0.3, "pointRadius": 2,
        "type": kind,
    }


def generate_extra_charts(rows: list[dict]) -> None:
    """
    Denní přírůstky a souhrn. Tyhle soubory na portálu existovaly, ale nic je
    negenerovalo — ebola_summary tam zůstal zamrzlý na datech z 13. 7. 2026,
    zatímco případů mezitím přibylo několikanásobně. Teď se počítají ze stejné
    kurátorované časové řady jako zbytek.
    """
    labels = [r["date"] for r in rows]

    def ints(key):
        return [int(r[key]) if str(r.get(key, "")).strip() else None for r in rows]

    save_json("ebola_daily", {
        "labels": labels,
        "datasets": [_series("Nově hlášené případy (den)", ints("new_confirmed_cases_reported"),
                             "13,110,253", fill=False, kind="bar")],
    })

    save_json("ebola_deaths_cumulative", {
        "labels": labels,
        "datasets": [_series("Úmrtí mezi potvrzenými případy (kumulativní)",
                             ints("confirmed_deaths_cumulative"), "220,53,69")],
    })

    last = rows[-1]
    cases_n = int(last["confirmed_cases_cumulative"])
    deaths_n = int(last["confirmed_deaths_cumulative"])
    save_json("ebola_summary", {
        "celkem_nakazenych": cases_n,
        "celkem_umrti": deaths_n,
        "cfr_pct": round(deaths_n / cases_n * 100, 1) if cases_n else None,
        "posledni_datum": last["date"],
    })


# Referenční trajektorie minulých epidemií: (den od prvního hlášeného případu,
# kumulativní počet potvrzených případů). Kurátorované hodnoty z literatury —
# historické epidemie jsou uzavřené, takže se nemění a nemá je odkud stahovat.
# Současná epidemie se sem doplňuje z naší vlastní časové řady, ne ručně.
HISTORICAL_TRAJECTORIES: dict[str, list[tuple[int, int]]] = {
    "Západní Afrika 2014–2016": [(0, 49), (24, 224), (85, 528), (99, 759)],
    "DRK 2018–2020":            [(0, 26), (4, 43), (59, 159), (97, 308)],
    "DRK 2020":                 [(0, 6), (40, 56), (95, 100), (100, 110)],
    "DRK 2025":                 [(0, 28), (31, 64), (88, 64)],
    "DRK 2012":                 [(0, 10), (17, 28), (45, 50), (98, 77)],
}

TRAJECTORY_WINDOW_DAYS = 100


def generate_trajectories(rows: list[dict]) -> None:
    """
    Srovnání rychlosti růstu epidemií v prvních ~100 dnech.

    Dvě věci, které tenhle graf dřív dělal špatně:

    1. Řada „DRK 2026“ byla vypsaná ručně a zamrzla na 1 031 případech, zatímco
       naše vlastní data ukazovala 6 100. Teď se počítá z `rows` — ze stejné
       řady, ze které žijí ostatní grafy — takže zastarat nemůže.

    2. Osa X byla kategorická: dny 0, 4, 6, 12, … 99, 100 se kreslily rovnoměrně,
       takže úsek 0→4 byl stejně široký jako 59→85. U grafu, který má porovnávat
       *rychlost* růstu, tím sklony křivek nic neznamenaly. Proto {x, y} body
       a `x_scale: "linear"` — vzdálenost na ose teď odpovídá počtu dnů.

    Řídké řady se navíc nesmí kreslit přes `null` v poli zarovnaném na společné
    popisky: sousední hodnoty pak nejsou sousední body a Chart.js mezi nimi
    úsečku nenakreslí. Každá řada proto nese jen své vlastní body.
    """
    day_zero = date.fromisoformat(rows[0]["date"])
    current = []
    for r in rows:
        day = (date.fromisoformat(r["date"]) - day_zero).days
        if day > TRAJECTORY_WINDOW_DAYS:
            break
        current.append({"x": day, "y": int(r["confirmed_cases_cumulative"])})

    datasets = [{"label": "DRK 2026", "data": current}]
    datasets += [
        {"label": label, "data": [{"x": d, "y": v} for d, v in points]}
        for label, points in HISTORICAL_TRAJECTORIES.items()
    ]

    # Logaritmická osa Y: současná epidemie je řádově větší než všechny historické
    # (6 100 proti 759 u nejhorší z nich), takže na lineární ose by se ty menší
    # slisovaly na nulu. Na logaritmické je navíc sklon přímo rychlost růstu —
    # tedy to, co má srovnání trajektorií vlastně ukázat.
    save_json("ebola_trajectories", {
        "x_scale": "linear",
        "x_unit": "den",
        "x_title": "Den od prvního hlášeného případu",
        "y_scale": "logarithmic",
        "datasets": datasets,
    })


def save_json(name: str, obj: dict) -> None:
    CHARTS_OUT.mkdir(parents=True, exist_ok=True)
    path = CHARTS_OUT / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    print(f"  [{name}] → {path}")


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

{{< chart id="ebolaTimeseries" src="/data/charts/ebola_timeseries.json" type="line" title="Ebola BDBV 2026 — kumulativní případy a úmrtí (DRC)" height="360" note="Kumulativní absolutní počty potvrzených případů a úmrtí od začátku epidemie, celá postižená oblast (DRK)." >}}

---

"""

# Stránka "Graf vývoje" ze zipu žádný graf neobsahuje — je to jen text. Grafy,
# které k ní patří, se na portálu generovaly, ale nikde nezobrazovaly.
GRAF_VYVOJ_BLOCK = """\

---

## Denně hlášené případy

{{< chart id="ebolaDaily" src="/data/charts/ebola_daily.json" type="bar" title="Ebola BDBV 2026 — nově hlášené případy za den (DRC)" height="320" note="Absolutní počty nově hlášených potvrzených případů za den — přímo hlášené hodnoty ze SitRepů, ne rozdíly kumulativ." >}}

## Kumulativní úmrtí

{{< chart id="ebolaDeaths" src="/data/charts/ebola_deaths_cumulative.json" type="line" title="Ebola BDBV 2026 — kumulativní úmrtí mezi potvrzenými případy (DRC)" height="320" note="Kumulativní absolutní počet úmrtí mezi laboratorně potvrzenými případy." >}}

## Srovnání s předchozími epidemiemi

Kumulativní počty případů podle počtu dnů od začátku epidemie. Srovnání je orientační — jednotlivá ohniska se liší dostupností testování i rozsahem sledovaného území.

{{< chart id="ebolaTrajectories" src="/data/charts/ebola_trajectories.json" type="line" title="Trajektorie vybraných ebolavirových epidemií" height="360" note="Kumulativní absolutní počty případů; osa X = dny od prvního hlášeného případu, osa Y logaritmická (epidemie se liší o řády)." >}}

---

"""

FRONTMATTER_INDEX = """\
---
title: "Ebola — Bundibugyo virus (DRC/Uganda 2026)"
description: "Situační report vypuknutí Bundibugyo viru v DRC a Ugandě 2026. Zdroj: IMG AV ČR / UJEP. Verze {version}, ověřeno {date_released}."
image: "/images/cards/ebola.svg"
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


def _insert_charts(body_md: str, block: str) -> str:
    """
    Vloží blok grafů za druhý H2. Když stránka druhý H2 nemá (kratší sekce),
    připojí ho na konec — dřív se v takovém případě graf tiše zahodil.
    """
    lines = body_md.split("\n")
    insert_at, h2_count = 0, 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            h2_count += 1
            if h2_count == 2:
                insert_at = i
                break
    if insert_at:
        lines.insert(insert_at, block)
        return "\n".join(lines)
    return body_md.rstrip() + "\n" + block


def generate_page(filename: str, html_text: str, citation: dict,
                  rows: list[dict] | None = None) -> None:
    slug, page_title = PAGE_MAP[filename]
    nav_pills, body_md = html_to_md(html_text, filename, rows)

    if filename == "index.html":
        fm = FRONTMATTER_INDEX.format(
            version=citation.get("version", "?"),
            date_released=citation.get("date_released", "?"),
        )
        body_md = _insert_charts(body_md, CHART_BLOCK)
    else:
        fm = FRONTMATTER_SUB.format(page_title=page_title)
        if filename == "graf-vyvoj.html":
            body_md = _insert_charts(body_md, GRAF_VYVOJ_BLOCK)

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
    generate_extra_charts(rows)
    generate_trajectories(rows)

    for filename, html_text in html_files.items():
        generate_page(filename, html_text, citation, rows)

    print("  Hotovo.")


def read_zip_file_raw(z: zipfile.ZipFile, suffix: str) -> str:
    for name in z.namelist():
        if name.endswith(suffix):
            with z.open(name) as f:
                return f.read().decode("utf-8")
    raise KeyError(f"Soubor *{suffix} nenalezen v ZIP")


if __name__ == "__main__":
    run()

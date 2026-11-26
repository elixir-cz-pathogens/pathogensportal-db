"""
Ověří registr patogenů proti autoritám — ENA (EMBL-EBI), Wikidata, MeSH (NLM).

Proč: identifikátory v `curated/pathogens.yaml` opisuje člověk a taxonomie se
mění (ICTV přejmenovává druhy virů skoro každý rok). Testy hlídají jen to, že
registr drží pohromadě sám se sebou; jestli číslo 11320 pořád znamená
„Influenza A virus“, ví jen zdroj. Tenhle skript se ho zeptá a vypíše rozdíly.

Nespouští se v pipeline ani v CI — potřebuje síť a jeho nález není chyba běhu,
ale úkol pro člověka: opravit záznam a přepsat `verified` na dnešek.

Co se porovnává:
  ENA taxonomie   scientific_name a rank uzlu `taxon`; `species` a `family` musí
                  být v jeho rodokmenu (lineage)
  Wikidata        položka z `ids.wikidata` má vlastnost P685 (NCBI taxonomy ID)
                  rovnou `taxon`, a `ids.mesh` je její P486
  MeSH            deskriptor z `ids.mesh` u NLM existuje (vypíše se jeho název)

Použití:
    python scripts/verify_pathogens.py                 # celý registr
    python scripts/verify_pathogens.py influenza-a tbe # jen vyjmenované záznamy

Návratový kód: 0 = vše sedí, 1 = aspoň jeden rozdíl, 2 = zdroj neodpověděl.
"""

import sys
import time

import requests

import pathogen_registry as registry

UA = "pathogen-portal-cz/1.0 (+https://pathogens.vm.cesnet.cz/)"
ENA_TAXON = "https://www.ebi.ac.uk/ena/taxonomy/rest/tax-id/{id}"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
MESH = "https://id.nlm.nih.gov/mesh/{id}.json"
TIMEOUT = 40
PAUSE = 0.2          # ohled na veřejné služby; registr má desítky záznamů, ne tisíce


class SourceUnavailable(RuntimeError):
    """Zdroj neodpověděl — není to rozdíl v registru, jen se nedalo ověřit."""


def _get(url: str, **kwargs):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=TIMEOUT, **kwargs)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise SourceUnavailable(f"{url}: {last}")


def check_taxon(p: dict, node: dict | None) -> list:
    """Rozdíly mezi záznamem a uzlem ENA taxonomie. Čistá funkce — testuje se bez sítě."""
    pid = p["id"]
    if node is None:
        return [f"{pid}: taxon {p['taxon']} v ENA taxonomii není"]
    out = []
    if node.get("scientificName") != p["scientific_name"]:
        out.append(f"{pid}: scientific_name {p['scientific_name']!r} ≠ ENA {node.get('scientificName')!r}")
    if node.get("rank") != p["rank"]:
        out.append(f"{pid}: rank {p['rank']!r} ≠ ENA {node.get('rank')!r}")
    lineage = [x.strip() for x in (node.get("lineage") or "").split(";") if x.strip()]
    for key in ("species", "family"):
        if p.get(key) and p[key] not in lineage:
            out.append(f"{pid}: {key} {p[key]!r} není v rodokmenu ENA ({' > '.join(lineage[-4:])})")
    return out


def check_wikidata(p: dict, items: dict) -> list:
    """
    `items`: QID → {"taxon": [...], "mesh": [...]} pro položky, které Wikidata
    vede pod taxonem záznamu. Čistá funkce.
    """
    pid, ids = p["id"], p.get("ids") or {}
    qid = ids.get("wikidata")
    out = []
    if qid is None:
        if items:
            out.append(f"{pid}: Wikidata má pro taxon {p['taxon']} položku {', '.join(sorted(items))}, "
                       f"registr žádnou — doplnit ids.wikidata?")
        if ids.get("mesh"):
            out.append(f"{pid}: ids.mesh bez ids.wikidata nejde ověřit")
        return out
    if qid not in items:
        out.append(f"{pid}: Wikidata {qid} nemá NCBI taxonomy ID {p['taxon']}")
        return out
    mesh = ids.get("mesh")
    if mesh and mesh not in items[qid]["mesh"]:
        out.append(f"{pid}: MeSH {mesh} není u Wikidata {qid} (má {items[qid]['mesh'] or 'žádný'})")
    return out


def wikidata_items(taxa: list) -> dict:
    """taxon → {QID: {"mesh": [...]}} jedním dotazem pro celý registr."""
    values = " ".join(f'"{t}"' for t in taxa)
    query = ("SELECT ?tax ?item ?mesh WHERE { VALUES ?tax {" + values + "} "
             "?item wdt:P685 ?tax. OPTIONAL { ?item wdt:P486 ?mesh } }")
    data = _get(WIKIDATA_SPARQL, params={"query": query, "format": "json"})
    out: dict = {}
    for b in (data or {}).get("results", {}).get("bindings", []):
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        slot = out.setdefault(int(b["tax"]["value"]), {}).setdefault(qid, {"mesh": []})
        if "mesh" in b:
            slot["mesh"].append(b["mesh"]["value"])
    return out


def main(argv: list) -> int:
    try:
        reg = registry.load()
    except registry.RegistryError as e:
        print(e, file=sys.stderr)
        return 1
    records = [p for p in reg["pathogens"] if not argv or p["id"] in argv]
    unknown = set(argv) - {p["id"] for p in records}
    if unknown:
        print(f"Neznámé id: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    findings = []
    try:
        wd = wikidata_items([p["taxon"] for p in records])
        for p in records:
            found = check_taxon(p, _get(ENA_TAXON.format(id=p["taxon"])))
            found += check_wikidata(p, wd.get(p["taxon"], {}))
            mesh = (p.get("ids") or {}).get("mesh")
            label = None
            if mesh:
                doc = _get(MESH.format(id=mesh))
                if doc is None:
                    found.append(f"{p['id']}: MeSH {mesh} u NLM neexistuje")
                else:
                    label = (doc.get("label") or {}).get("@value")
            print(f"  {'✗' if found else '✓'} {p['id']:<18} {p['taxon']:>8}  {p['scientific_name']}"
                  + (f"  · MeSH {mesh}: {label}" if label else ""))
            findings += found
            time.sleep(PAUSE)
    except SourceUnavailable as e:
        print(f"Zdroj neodpověděl, ověření je neúplné: {e}", file=sys.stderr)
        return 2

    if findings:
        print(f"\n{len(findings)} rozdílů:")
        for f in findings:
            print(f"  - {f}")
        return 1
    print(f"\nVšech {len(records)} záznamů sedí se zdroji. Po kontrole přepište `verified` na dnešní datum.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

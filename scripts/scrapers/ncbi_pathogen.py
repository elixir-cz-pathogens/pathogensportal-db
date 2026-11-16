"""
NCBI Pathogen Detection — geny rezistence u českých bakteriálních izolátů.
Zdroj: ftp.ncbi.nlm.nih.gov/pathogen/Results/<organismus>/latest_snps/AMR/
Licence: "NCBI itself places no restrictions on the use or distribution of the
         data contained therein" (politika NCBI pro molekulární data). Původci
         jednotlivých záznamů si práva nárokovat mohou, NCBI je neposuzuje.
Aktualizace: NCBI přepočítává průběžně (nové vydání PDG… každých pár dní)

Co to je: NCBI nad všemi veřejnými bakteriálními genomy samo pouští AMRFinderPlus,
který v sestaveném genomu hledá známé geny rezistence, a výsledek zveřejňuje jako
tabulku. Portál tedy nic nepočítá — jen z tabulky vybere české izoláty.

⚠️ Soubory mají 60–190 MB na organismus. Stahují se proudem a na disk se ukládají
jen české řádky (desítky kB); stejný postup jako u `osoby.csv` z MZČR, kde se
z 331 MB nechává 2 MB agregátu. Bez toho by repozitář narostl o stovky MB dat,
která nepotřebujeme.

⚠️ Záznam = sekvenovaný izolát, ne pacient. Sekvenují se přednostně kmeny vybrané
kvůli rezistenci a část vzorků je z odpadních vod, takže podíly NEJSOU výskytem
rezistence v populaci. Hlídá to `compute_genomics.py`, který z těchto čísel
nepočítá procenta.

Výstup:
  ncbi/ncbi_pd_cz.csv       jeden řádek na český izolát (vybrané sloupce)
  ncbi/ncbi_pd_release.json která vydání PDG… byla stažena (pro doložení čísel)
"""

import csv
import io
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests

FTP = "https://ftp.ncbi.nlm.nih.gov/pathogen/Results"
UA = "pathogen-portal-cz/1.0 (+https://pathogens.vm.cesnet.cz/)"
TIMEOUT = 600
RETRIES = 2

# Organismy, u kterých má rezistence pro portál smysl a kterých je z Česka dost.
# Klíč = adresář u NCBI, hodnota = id do výstupu (shodné s curated/genomics_taxa.yaml).
ORGANISMS = {
    "Klebsiella": "klebsiella",
    "Enterococcus_faecium": "e-faecium",
    "Staphylococcus_aureus": "s-aureus",
    "Listeria": "listeria",
}

# Sloupce, které se z tabulky nechávají. Zbytek (PFGE vzory, interní id, souřadnice)
# portál nepoužívá a jen by nafukoval soubor.
KEEP = ["target_acc", "biosample_acc", "asm_acc", "scientific_name", "geo_loc_name",
        "collection_date", "host", "isolation_source", "epi_type", "AMR_genotypes",
        "asm_stats_contig_n50", "asm_stats_n_contig", "number_amr_genes", "sra_release_date"]

CZECH = re.compile(r"\bCzech", re.IGNORECASE)


def _latest_file(organism: str) -> str:
    """Jméno nejnovějšího souboru PDG…amr.metadata.tsv v adresáři AMR/."""
    r = requests.get(f"{FTP}/{organism}/latest_snps/AMR/", headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    names = sorted(set(re.findall(r'href="(PDG[0-9.]+\.amr\.metadata\.tsv)"', r.text)))
    if not names:
        raise ValueError(f"{organism}: v adresáři AMR/ není žádný amr.metadata.tsv — změnil se FTP")
    return names[-1]


def _stream_czech(organism: str, filename: str) -> list[dict]:
    """
    Projde soubor po řádcích a vrátí jen české izoláty.

    Filtruje se na syrovém textu před parsováním CSV: `geo_loc_name` je sice
    sloupec, ale načíst 190 MB do paměti kvůli stovce řádků nemá smysl. Řádek,
    který slovo Czech obsahuje jinde (třeba v názvu projektu), projde přes
    hrubý filtr a odřízne ho až kontrola sloupce níž.
    """
    url = f"{FTP}/{organism}/latest_snps/AMR/{filename}"
    last = None
    for attempt in range(RETRIES):
        try:
            with requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, stream=True) as r:
                r.raise_for_status()
                r.encoding = "utf-8"
                # iter_lines, ne TextIOWrapper nad r.raw: urllib3 syrový proud na
                # konci těla odpovědi zavře a wrapper si pak sáhne na zavřený soubor.
                wanted, header_seen = [], False
                for line in r.iter_lines(decode_unicode=True):
                    if line is None:
                        continue
                    if not header_seen:
                        wanted.append(line)
                        header_seen = True
                    elif CZECH.search(line):
                        wanted.append(line)
            break
        except requests.RequestException as e:
            last = e
            time.sleep(5)
    else:
        raise RuntimeError(f"{organism}: stažení selhalo: {last}")

    reader = csv.DictReader(io.StringIO("\n".join(wanted)), delimiter="\t")
    if "geo_loc_name" not in (reader.fieldnames or []):
        raise ValueError(f"{organism}: v tabulce chybí sloupec geo_loc_name — změnil se formát")
    rows = []
    for rec in reader:
        if not CZECH.search(rec.get("geo_loc_name") or ""):
            continue
        rows.append({k: ("" if rec.get(k) in (None, "NULL") else rec[k]) for k in KEEP})
    return rows


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows, releases = [], {}
    for organism, key in ORGANISMS.items():
        filename = _latest_file(organism)
        releases[key] = {"organism": organism, "release": filename.split(".amr")[0]}
        rows = _stream_czech(organism, filename)
        for r in rows:
            r["id"] = key
        all_rows.extend(rows)
        with_genes = sum(1 for r in rows if r["AMR_genotypes"])
        print(f"  [ncbi_pathogen] {organism} ({releases[key]['release']}): "
              f"{len(rows)} českých izolátů, z toho {with_genes} s nálezem genů")

    path = output_dir / "ncbi_pd_cz.csv"
    pd.DataFrame(all_rows, columns=["id"] + KEEP).to_csv(path, index=False, encoding="utf-8")
    rel = output_dir / "ncbi_pd_release.json"
    rel.write_text(json.dumps({"fetched_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "releases": releases}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"  [ncbi_pathogen] celkem {len(all_rows)} českých izolátů → {path}")
    return [str(path), str(rel)]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "ncbi")

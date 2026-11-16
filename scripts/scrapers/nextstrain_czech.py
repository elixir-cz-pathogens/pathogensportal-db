"""
Nextstrain — kolik českých kmenů je ve veřejných stromech příbuznosti.
Zdroj: data.nextstrain.org (hotové stromy ve formátu Auspice v2)
Licence: kód Nextstrainu AGPL, snímky obrazovky CC BY 4.0 s uvedením
         nextstrain.org. Podkladová data jednotlivých stromů mají vlastní
         podmínky (GISAID, GenBank, Pathoplexus) — proto se z nich přebírají
         jen souhrnné počty a odkaz, žádné sekvence. Viz sources.yaml.
Aktualizace: podle stromu (chřipkové denně až týdně, některé stojí)

Co se z každého stromu bere: počet listů celkem, počet listů s českou zemí
odběru, jejich linie, rozsah dat odběru a datum, kdy byl strom naposledy
přepočítán. Nic víc — sekvence ani jejich jména se neukládají.

Proč i stromy, kde je českých kmenů nula (spalničky, RSV): nula je informace.
Oba tyhle stromy se obnovují týdně a stojí na zcela otevřených datech, do
kterých může přispět kdokoli; že v nich český vzorek není, je zjištění.

Výstup:
  nextstrain/nextstrain_cz.csv       jeden řádek na strom
  nextstrain/nextstrain_cz_clades.csv jeden řádek na strom × linii (české kmeny)
"""

import gzip
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

BASE = "https://data.nextstrain.org"
UA = "pathogen-portal-cz/1.0 (+https://pathogens.vm.cesnet.cz/)"
TIMEOUT = 180
PAUSE = 0.5
RETRIES = 3

TREES_FILE = Path(__file__).resolve().parents[2] / "curated" / "genomics_trees.yaml"

# Hodnota pole `country` u českých kmenů. Stromy používají starý název; nový
# tvar je tu kvůli tomu, aby přejmenování u zdroje nevyhodilo české kmeny potichu.
COUNTRY_VALUES = ("Czech Republic", "Czechia")


def _fetch(build: str) -> dict:
    url = f"{BASE}/{build}.json"
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"},
                             timeout=TIMEOUT)
            r.raise_for_status()
            body = r.content
            if body[:2] == b"\x1f\x8b":          # některé stromy chodí zabalené
                body = gzip.decompress(body)
            return json.loads(body)
        except (requests.RequestException, json.JSONDecodeError, OSError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Nextstrain {build} nedostupný: {last}")


def _tips(tree: dict) -> list[dict]:
    """Listy stromu. Strom může být i seznam kořenů, proto obojí."""
    out, stack = [], (list(tree) if isinstance(tree, list) else [tree])
    while stack:
        node = stack.pop()
        children = node.get("children")
        if children:
            stack.extend(children)
        else:
            out.append(node)
    return out


def _attr(node: dict, key: str):
    value = node.get("node_attrs", {}).get(key)
    return value.get("value") if isinstance(value, dict) else value


def _decimal_to_date(x) -> str:
    """Nextstrain datuje desetinným rokem (2026.21); zpět na den stačí přes délku roku."""
    if x is None:
        return ""
    year = int(x)
    length = 366 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 365
    return (date(year, 1, 1) + timedelta(days=round((x - year) * length))).isoformat()


def download(output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trees = yaml.safe_load(TREES_FILE.read_text(encoding="utf-8"))
    rows, clade_rows = [], []

    for spec in trees:
        data = _fetch(spec["build"])
        meta = data.get("meta", {})
        tips = _tips(data["tree"])
        cz = [t for t in tips if str(_attr(t, "country")) in COUNTRY_VALUES]
        dates = sorted(d for d in (_attr(t, "num_date") for t in cz) if d is not None)
        rows.append({
            "id": spec["id"],
            "build": spec["build"],
            "listu_celkem": len(tips),
            "listu_cz": len(cz),
            "odber_od": _decimal_to_date(dates[0]) if dates else "",
            "odber_do": _decimal_to_date(dates[-1]) if dates else "",
            "strom_prepocitan": meta.get("updated", ""),
            "podklad": "; ".join(p.get("name", "") for p in meta.get("data_provenance", [])),
        })

        # Linie českých kmenů. `clade_key` se mezi stromy liší, proto je v konfiguraci;
        # když pole ve stromu není, zůstane sloupec prázdný a strom se nerozpadne.
        key = spec.get("clade_key")
        counts: dict[tuple[str, str], int] = {}
        for t in cz:
            clade = _attr(t, key) if key else None
            host = _attr(t, spec["host_key"]) if spec.get("host_key") else None
            bucket = (str(clade) if clade is not None else "",
                      str(host) if host is not None else "")
            counts[bucket] = counts.get(bucket, 0) + 1
        for (clade, host), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            clade_rows.append({"id": spec["id"], "linie": clade, "hostitel": host, "pocet": n})

        if cz and key and all(_attr(t, key) is None for t in cz):
            print(f"  [nextstrain] ⚠ {spec['id']}: pole '{key}' ve stromu není — linie chybí")
        print(f"  [nextstrain] {spec['id']}: {len(cz)} z {len(tips)} listů, "
              f"strom z {meta.get('updated', '?')}")
        time.sleep(PAUSE)

    main = output_dir / "nextstrain_cz.csv"
    pd.DataFrame(rows).to_csv(main, index=False, encoding="utf-8")
    clades = output_dir / "nextstrain_cz_clades.csv"
    pd.DataFrame(clade_rows, columns=["id", "linie", "hostitel", "pocet"]).to_csv(
        clades, index=False, encoding="utf-8")
    # Čas stažení zvlášť: stromy mají každý svoje datum přepočtu, ale
    # compute_genomics podle tohohle pozná, jestli se stažení tentokrát povedlo.
    fetch = output_dir / "nextstrain_cz_fetch.json"
    fetch.write_text(json.dumps(
        {"fetched_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
    return [str(main), str(clades), str(fetch)]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("DATA_DIR", str(root / "data")))
    download(data_dir / "nextstrain")

"""
ÚZIS — týdenní počty vybraných infekčních nemocí (datový souhrn NR-27-04).

Zdroj:  „Meziroční srovnání počtu infekčních nemocí po týdnech v ČR“,
        https://www.nzip.cz/data/2620-infekcni-nemoci-mezirocni-srovnani-datovy-souhrn
        Soubory leží na sdíleném úložišti ÚZIS; odkaz „/download“ vrátí celý
        obsah jako jeden ZIP (kolem 32 MB) bez přihlášení.
Licence: stránka souhrnu ani metodický popis licenci neuvádějí, uvádějí jen
        citaci — viz sources.yaml.
Výstup: data/isin/isin_tydenni.csv        diagnoza (MKN-10), kraj_kod (CZ = celá ČR,
                                          jinak NUTS3), rok a tyden (ISO), vek
                                          (věková skupina nebo „Celkem“), pocet
                                          (všechny záznamy), pocet_ews (z toho z EWS)
        data/isin/isin_tydenni_stav.json  složka a stav dat u zdroje, názvy diagnóz

Proč vedle uzis_isin.py: otevřené CSV ISIN (NR-27-01) má všechny diagnózy, ale
obnovuje se jednou ročně a končí prosincem minulého roku. Tento souhrn má jen
11 diagnóz, zato po týdnech až do minulého týdne a zvlášť počet záznamů
založených na hlášení EWS — bez něj se roky od 2025 nedají číst (viz
methodology_changes.yaml, `isin-ews-2025-07`).

Tvar zdroje (ověřeno na složkách 2026-10-05 až 2026-11-16):
  - ZIP má složky `Infekce/<pondělí>/`; plné jsou jen čtyři až pět posledních,
    budoucí pondělky jsou založené dopředu a prázdné. Každý soubor nese celou
    historii posledních pěti let, starší složky tedy nejsou potřeba.
  - Jeden XLSX na diagnózu, 15 listů: ČR a 14 krajů. Název kraje je v buňce A9;
    jméno listu se nepoužívá (list Královéhradeckého kraje se jmenuje
    „KVK … (2)“).
  - Na listu leží roky vedle sebe jako bloky sloupců. Blok začíná sloupcem
    „ISO_WEEK“ (rok je o řádek výš), následuje datum a pak dvojice sloupců
    ISIN / EWS pro každou věkovou skupinu a pro „Celkem“. Mezi bloky i uvnitř
    bloku jsou místy prázdné sloupce, proto se poloha čte z hlavičky, ne
    z pevných čísel sloupců.
  - „ISIN“ = všechny záznamy v ISIN, „EWS“ = z toho záznamy založené na
    hlášení EWS (ne další případy navíc).
  - Řádky týdnů končí řádkem „Celkem/Total“; uvnitř bloku může být prázdný
    řádek. Datum u řádku „Celkem/Total“ je ve zdroji zastaralé a nečte se.

Starší roky: souhrn nese jen posledních pět let. Rok, který z něj vypadne, se
v CSV ponechá z minulého stažení (`keep_older_years`) — signály z týdenního
souhrnu (detect_anomalies.py --zdroj tydenni) potřebují aspoň tři roky historie
před zavedením EWS a bez toho by v roce 2027 přišly o rok 2022. Ponechávají se
jen roky STARŠÍ než nejstarší rok nového stažení; rok, který zdroj ještě nese,
se vždy bere ze zdroje.
"""

import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

URL = "https://share.uzis.cz/s/p6CPnibmQDckJ5r/download"
LANDING = "https://www.nzip.cz/data/2620-infekcni-nemoci-mezirocni-srovnani-datovy-souhrn"

CSV_NAME = "isin_tydenni.csv"
STATE_NAME = "isin_tydenni_stav.json"

# Souhrn vychází každé pondělí. Tři týdny bez nové složky = zdroj se přestal
# obnovovat nebo se přestěhoval; tiché kreslení starých „aktuálních“ týdnů by
# bylo horší než hlasité selhání.
MAX_AGE_DAYS = 21

REGION_TO_NUTS3 = {
    "ČR":                   "CZ",
    "Hlavní město Praha":   "CZ010",
    "Středočeský kraj":     "CZ020",
    "Jihočeský kraj":       "CZ031",
    "Plzeňský kraj":        "CZ032",
    "Karlovarský kraj":     "CZ041",
    "Ústecký kraj":         "CZ042",
    "Liberecký kraj":       "CZ051",
    "Královéhradecký kraj": "CZ052",
    "Pardubický kraj":      "CZ053",
    "Kraj Vysočina":        "CZ063",
    "Jihomoravský kraj":    "CZ064",
    "Olomoucký kraj":       "CZ071",
    "Zlínský kraj":         "CZ072",
    "Moravskoslezský kraj": "CZ080",
}

TOTAL = "Celkem"
# Názvy diagnóz jdou do stavového souboru, ne do každého z půl milionu řádků.
COLUMNS = ["diagnoza", "kraj_kod", "rok", "tyden", "vek", "pocet", "pocet_ews"]

_WEEK = re.compile(r"^(\d{4})-(\d{2})$")
_STAMP = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _stamp(cell) -> str | None:
    """„Stav k datu: 15.11.2026 05:34“ → „2026-11-15T05:34“."""
    m = _STAMP.search(_text(cell))
    if not m:
        return None
    d, mo, y, h, mi = (int(x) for x in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}"


def _blocks(header: tuple, years: tuple, channels: tuple) -> list[dict]:
    """
    Bloky roků na listu: kde začínají a ve kterých sloupcích má která věková
    skupina dvojici ISIN / EWS.
    """
    starts = [i for i, v in enumerate(header) if _text(v) == "ISO_WEEK"]
    if not starts:
        raise ValueError("na listu chybí hlavička ISO_WEEK")
    # Rok bez hlavičky pod sebou by z dat zmizel bez chyby — a první blok je běžící rok.
    year_cols = [i for i, v in enumerate(years) if re.fullmatch(r"\d{4}", _text(v))]
    if year_cols != starts:
        raise ValueError("řádek s roky a hlavičky ISO_WEEK si neodpovídají — změnila se stavba listu?")
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(header)
        year = _text(years[start])
        if not year.isdigit():
            raise ValueError(f"nad sloupcem ISO_WEEK není rok: {years[start]!r}")
        groups = {}
        for col in range(start + 2, end):
            label = _text(header[col]).split("\n")[0].strip()
            if not label:
                continue
            label = TOTAL if label.lower().startswith("celkem") else label
            if _text(channels[col]) != "ISIN" or _text(channels[col + 1]) != "EWS":
                raise ValueError(f"sloupec „{label}“ roku {year} nemá dvojici ISIN / EWS")
            groups[label] = col
        if TOTAL not in groups or len(groups) < 2:
            raise ValueError(f"blok roku {year} nemá sloupec Celkem nebo věkové skupiny")
        blocks.append({"year": int(year), "start": start, "groups": groups})
    return blocks


def parse_sheet(rows: list[tuple]) -> tuple[str, list[dict]]:
    """
    Jeden list → (název území z A9, řádky {rok, tyden, vek, pocet, pocet_ews}).

    Kontroluje to, na čem stojí všechno další: týdny jdou po sobě od prvního,
    věkové skupiny dávají dohromady „Celkem“ a EWS není víc než všech záznamů.
    """
    region = _text(rows[8][0])
    blocks = _blocks(rows[10], rows[9], rows[11])
    out = []
    for block in blocks:
        expected = 1
        for row in rows[12:]:
            key = _text(row[block["start"]])
            if key.lower().startswith("celkem"):
                break
            m = _WEEK.match(key)
            if not m:
                continue                      # prázdný řádek uvnitř bloku
            year, week = int(m.group(1)), int(m.group(2))
            if year != block["year"] or week != expected:
                raise ValueError(f"{region}: čekal jsem týden {block['year']}-{expected:02d}, je tam {key}")
            expected += 1
            total = age_sum = 0
            for label, col in block["groups"].items():
                n, ews = row[col], row[col + 1]
                if not isinstance(n, (int, float)) or not isinstance(ews, (int, float)):
                    raise ValueError(f"{region} {key} „{label}“: místo počtu je {n!r} / {ews!r}")
                n, ews = int(n), int(ews)
                if ews > n:
                    raise ValueError(f"{region} {key} „{label}“: EWS {ews} je víc než všech záznamů {n}")
                if label == TOTAL:
                    total = n
                else:
                    age_sum += n
                out.append({"rok": year, "tyden": week, "vek": label, "pocet": n, "pocet_ews": ews})
            if age_sum != total:
                raise ValueError(f"{region} {key}: věkové skupiny dávají {age_sum}, Celkem je {total}")
        if expected == 1:
            raise ValueError(f"{region}: blok roku {block['year']} nemá žádný týden")
    return region, out


def parse_workbook(content: bytes) -> tuple[pd.DataFrame, dict]:
    """Jeden XLSX (jedna diagnóza) → dlouhá tabulka a hlavička souboru."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    info, frames, seen = None, [], set()
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 13:
            raise ValueError(f"list „{ws.title}“ je kratší než hlavička")
        if info is None:
            code = _text(rows[4][0]).split(":")[-1].strip()
            name_en = next((_text(v) for v in rows[3][1:] if _text(v)), "")
            info = {"diagnoza": code, "diagnoza_nazev": _text(rows[3][0]),
                    "diagnoza_nazev_en": name_en,
                    "zpracovano": _stamp(rows[1][0]), "stav_k": _stamp(rows[2][0])}
            if not info["diagnoza"] or not info["diagnoza_nazev"] or not info["stav_k"]:
                raise ValueError(f"list „{ws.title}“: nečitelná hlavička souboru")
        region, records = parse_sheet(rows)
        if region not in REGION_TO_NUTS3:
            raise ValueError(f"list „{ws.title}“: neznámé území {region!r} v buňce A9")
        if region in seen:
            raise ValueError(f"území {region!r} je v souboru dvakrát")
        seen.add(region)
        df = pd.DataFrame(records)
        df["kraj_kod"] = REGION_TO_NUTS3[region]
        frames.append(df)
    missing = set(REGION_TO_NUTS3) - seen
    if missing:
        raise ValueError(f"v souboru chybí území: {sorted(missing)}")
    df = pd.concat(frames, ignore_index=True)
    df["diagnoza"] = info["diagnoza"]
    return df[COLUMNS], info


def latest_folder(names: list[str]) -> tuple[str, list[str]]:
    """Nejnovější složka, ve které opravdu jsou soubory (budoucí jsou prázdné)."""
    by_folder: dict[str, list[str]] = {}
    for name in names:
        m = re.match(r"^Infekce/(\d{4}-\d{2}-\d{2})/[^/]+\.xlsx$", name)
        if m:
            by_folder.setdefault(m.group(1), []).append(name)
    if not by_folder:
        raise ValueError("v archivu není žádná složka Infekce/<datum>/ se soubory XLSX — změnila se stavba úložiště?")
    folder = max(by_folder)
    return folder, sorted(by_folder[folder])


def parse_archive(content: bytes) -> tuple[pd.DataFrame, dict]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        folder, names = latest_folder(z.namelist())
        frames, infos = [], []
        for name in names:
            try:
                df, info = parse_workbook(z.read(name))
            except ValueError as e:
                raise ValueError(f"{name.rsplit('/', 1)[-1]}: {e}") from e
            frames.append(df)
            infos.append(info)
    df = pd.concat(frames, ignore_index=True)
    dup = df["diagnoza"].nunique() != len(frames)
    if dup:
        raise ValueError("dva soubory ve složce mají stejný kód MKN-10")
    state = {
        "slozka": folder,
        # Nejstarší ze stavů: žádný soubor není čerstvější, než tvrdíme.
        "stav_k": min(i["stav_k"] for i in infos),
        "zpracovano": min(i["zpracovano"] or i["stav_k"] for i in infos),
        # Kód MKN-10 → název, jak ho píše zdroj (česky velkými písmeny, anglicky).
        "diagnozy": {i["diagnoza"]: {"cs": i["diagnoza_nazev"], "en": i["diagnoza_nazev_en"]}
                     for i in sorted(infos, key=lambda i: i["diagnoza"])},
    }
    return df, state


def keep_older_years(new: pd.DataFrame, old_path: Path) -> pd.DataFrame:
    """
    Nová tabulka doplněná o roky, které už zdroj nenese, z minulého CSV.

    Po diagnózách: rok se doplní jen u diagnózy, která je i v novém stažení, a jen
    když je starší než její nejstarší nový rok. Minulé CSV, které nejde přečíst
    nebo má jiné sloupce, se nepoužije — raději kratší historie než smíchaná data.
    """
    if not old_path.exists():
        return new
    try:
        old = pd.read_csv(old_path)
    except (OSError, ValueError) as e:
        print(f"  [uzis_isin_weekly] minulé CSV nejde přečíst ({e}) — starší roky nedoplňuji")
        return new
    if list(old.columns) != COLUMNS:
        print("  [uzis_isin_weekly] minulé CSV má jiné sloupce — starší roky nedoplňuji")
        return new
    first = new.groupby("diagnoza")["rok"].min()
    keep = old[old["diagnoza"].map(first).gt(old["rok"])]
    if keep.empty:
        return new
    years = sorted(int(y) for y in keep["rok"].unique())
    print(f"  [uzis_isin_weekly] ponechávám z minulého stažení roky, které zdroj už nenese: {years}")
    return (pd.concat([keep, new], ignore_index=True)
            .sort_values(["diagnoza", "kraj_kod", "rok", "tyden", "vek"], kind="stable")
            .reset_index(drop=True))


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def download(output_dir: Path, today: date | None = None) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    csv_path, state_path = output_dir / CSV_NAME, output_dir / STATE_NAME
    # run_all.py podle toho pozná, jestli tenhle běh zdroj opravdu ověřil.
    download.source_checked = True

    # Souhrn se mění jednou týdně, pipeline běží každou hodinu. Dokud máme
    # složku z tohoto týdne, 32 MB se znovu netahá.
    if csv_path.exists() and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("slozka", "") >= _monday(today).isoformat():
            print(f"  [uzis_isin_weekly] složka {state['slozka']} je z tohoto týdne — nestahuji")
            # Soubory vracíme, ale zdroje jsme se neptali: opravu vydanou pod
            # stejnou složkou bychom neviděli, takže to ověření není.
            download.source_checked = False
            return [str(csv_path), str(state_path)]

    print(f"  [uzis_isin_weekly] stahuji {URL}")
    resp = requests.get(URL, timeout=300)
    resp.raise_for_status()
    df, state = parse_archive(resp.content)

    age = (today - date.fromisoformat(state["slozka"])).days
    if age > MAX_AGE_DAYS:
        raise ValueError(f"nejnovější složka souhrnu je {state['slozka']} ({age} dní stará) — zdroj se neobnovuje")

    state["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    df = keep_older_years(df, csv_path)
    df.to_csv(csv_path, index=False, encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    last = df[df["rok"] == df["rok"].max()]
    print(f"  [uzis_isin_weekly] složka {state['slozka']}, stav k {state['stav_k']}: "
          f"{df['diagnoza'].nunique()} diagnóz, {len(df):,} řádků, "
          f"poslední týden {int(last['rok'].max())}-{int(last['tyden'].max()):02d} → {csv_path}")
    return [str(csv_path), str(state_path)]


if __name__ == "__main__":
    download(Path(__file__).resolve().parents[2] / "data" / "isin")

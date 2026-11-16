"""Genomická data z Česka (compute_genomics.py) — přehled archivu, linie, rezistence.

Hlídá pravidla, která by se v těchhle textech porušila nejsnáz: počet záznamů
vydávaný za nemocnost, podíly spočítané z nenáhodného výběru izolátů, zastoupení
linií z hrstky kmenů a ptačí chřipka bez věty, že vzorky nejsou od člověka.
"""
import json
import re

import pandas as pd
import pytest

import compute_genomics as cg

# Slova, která by z počtu záznamů udělala tvrzení o nemocnosti nebo o přenosu.
# Zápory se kontrolují zvlášť: „není nemocnost“ a „neříkají, jak častá je rezistence
# u pacientů“ jsou právě ty věty, které tam být MAJÍ.
FORBIDDEN = re.compile(r"nakažen|výskyt|prevalenc|epidemi|ohnisk|šíř|zavleč|krát\b|násob",
                       re.IGNORECASE)
ALLOWED_NEGATIONS = ("není nemocnost", "neříkají")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for name in ("ENA_DIR", "TREE_DIR", "NCBI_DIR"):
        monkeypatch.setattr(cg, name, tmp_path / name.split("_")[0].lower())
        (tmp_path / name.split("_")[0].lower()).mkdir(exist_ok=True)
    monkeypatch.setattr(cg, "OUTPUT_DIR", tmp_path / "out")
    return tmp_path


def _ena(ws, runs_rows, taxa_rows, totals=None):
    ena = ws / "ena"
    pd.DataFrame(runs_rows).to_csv(ena / "ena_cz_runs.csv", index=False)
    pd.DataFrame(taxa_rows).to_csv(ena / "ena_cz_taxa.csv", index=False)
    pd.DataFrame([{"tax_division": "VRL", "sekvenci": 7, "skupina_cs": "viry", "skupina_en": "viruses"}]
                 ).to_csv(ena / "ena_cz_divisions.csv", index=False)
    (ena / "ena_cz_totals.json").write_text(json.dumps(totals or {
        "samples": 100, "runs": len(runs_rows), "sequences": 7,
        "run_studies": 3, "sequence_studies": 2, "fetched_at": "2026-11-13T18:00:00Z"}),
        encoding="utf-8")


def _run(acc="SRR1", species="Klebsiella pneumoniae", collected="2020-05-04", public="2023-06-01",
         platform="ILLUMINA", bases="600000000", **extra):
    row = {"run_accession": acc, "sample_accession": "SAM1", "study_accession": "PRJ1",
           "scientific_name": species, "tax_id": "573", "collection_date": collected,
           "first_public": public, "instrument_platform": platform, "instrument_model": "MiSeq",
           "library_strategy": "WGS", "library_layout": "PAIRED", "base_count": bases,
           "read_count": "100", "host": "Homo sapiens", "isolation_source": "urine",
           "lat": "", "location": "", "strain": "", "isolate": "", "host_sex": "",
           "host_status": ""}
    row.update(extra)
    return row


def _taxon(tid, runs=0, seqs=0, last_run="", last_seq=""):
    return {"id": tid, "taxon": 1, "behy": runs, "behy_posledni": last_run, "behy_prvni": "",
            "sekvence": seqs, "sekvence_posledni": last_seq, "sekvence_prvni": ""}


def _trees(ws, rows, clades):
    pd.DataFrame(rows).to_csv(ws / "tree" / "nextstrain_cz.csv", index=False)
    pd.DataFrame(clades, columns=["id", "linie", "hostitel", "pocet"]).to_csv(
        ws / "tree" / "nextstrain_cz_clades.csv", index=False)


def _tree(tid, build, cz, tips=1000, updated="2026-11-13", frm="2026-01-05", to="2026-03-02",
          provenance="GISAID"):
    return {"id": tid, "build": build, "listu_celkem": tips, "listu_cz": cz,
            "odber_od": frm if cz else "", "odber_do": to if cz else "",
            "strom_prepocitan": updated, "podklad": provenance}


def _ncbi(ws, rows, releases=None):
    pd.DataFrame(rows).to_csv(ws / "ncbi" / "ncbi_pd_cz.csv", index=False)
    (ws / "ncbi" / "ncbi_pd_release.json").write_text(json.dumps({
        "fetched_at": "2026-11-13T18:00:00Z",
        "releases": releases or {"klebsiella": {"organism": "Klebsiella", "release": "PDG000000012.2578"}},
    }), encoding="utf-8")


def _isolate(key="klebsiella", genes="", source="urine", collected="2021-03-01", **extra):
    row = {"id": key, "target_acc": "PDT1", "biosample_acc": "SAM1", "asm_acc": "GCA_1",
           "scientific_name": "Klebsiella pneumoniae", "geo_loc_name": "Czech Republic",
           "collection_date": collected, "host": "Homo sapiens", "isolation_source": source,
           "epi_type": "clinical", "AMR_genotypes": genes, "asm_stats_contig_n50": "250000",
           "asm_stats_n_contig": "100", "number_amr_genes": "17", "sra_release_date": ""}
    row.update(extra)
    return row


# ── Formátování ──────────────────────────────────────────────────────────────

def test_datum_se_pise_cesky_i_anglicky():
    assert cg.czech_date("2026-09-30", "cs") == "30. 9. 2026"
    assert cg.czech_date("2026-09-30", "en") == "30 September 2026"
    assert cg.czech_date("", "cs") == "" and cg.czech_date("nesmysl", "cs") == "nesmysl"
    assert cg.month_label("2021-12-05", "cs") == "12/2021"


def test_geny_se_ctou_bez_stavu():
    assert cg._genes("blaKPC-2=COMPLETE,fosA=PARTIAL") == {"blaKPC-2", "fosA"}
    assert cg._genes("") == set() and cg._genes("mecA") == {"mecA"}


# ── Přehled archivu ──────────────────────────────────────────────────────────

def test_prehled_bez_dat_nespadne(ws):
    assert cg.build_overview() is None


def test_prehled_scita_prirustky_po_letech_a_technologiich(ws):
    _ena(ws, [_run("A", public="2025-01-01"), _run("B", public="2025-02-01", platform="OXFORD_NANOPORE"),
              _run("C", public="2025-03-01", platform="PACBIO_SMRT"), _run("D", public="2026-01-01")],
         [_taxon("klebsiella", runs=4, last_run="2026-01-01")])
    out = cg.build_overview()
    rows = {r["year"]: r for r in out["growth"]["rows"]}
    assert (rows["2025"]["ILLUMINA"], rows["2025"]["OXFORD_NANOPORE"], rows["2025"]["other"]) == (1, 1, 1)
    assert rows["2025"]["total"] == 3 and rows["2025"]["partial"] is False
    # Běžící rok je neúplný a musí to být poznat, jinak vypadá jako propad.
    assert rows["2026"]["partial"] is True


def test_patogen_bez_zaznamu_jde_mezi_nulove(ws):
    _ena(ws, [_run()], [_taxon("klebsiella", runs=1, last_run="2026-01-01"), _taxon("gonorrhoea")])
    out = cg.build_overview()
    nothing = {x["id"] for x in out["none"]}
    rows = {r["id"] for g in out["groups"] for r in g["rows"]}
    assert "gonorrhoea" in nothing and "gonorrhoea" not in rows
    assert "klebsiella" in rows and "klebsiella" not in nothing


def test_stary_zaznam_nese_znacku_stari(ws):
    _ena(ws, [_run()], [_taxon("influenza-a", seqs=950, last_seq="2021-12-05"),
                        _taxon("klebsiella", runs=1, last_run="2026-07-11")])
    rows = {r["id"]: r for g in cg.build_overview()["groups"] for r in g["rows"]}
    assert rows["influenza-a"]["stale_since"] == 2021
    assert rows["klebsiella"]["stale_since"] is None


def test_vyplnenost_poli_pocita_prazdne_hodnoty_zdroje(ws):
    # ENA píše místo prázdna i „missing“ nebo „not provided“ — to není vyplněné.
    _ena(ws, [_run("A", host="Homo sapiens"), _run("B", host="missing"), _run("C", host=""),
              _run("D", host="not provided")],
         [_taxon("klebsiella", runs=4, last_run="2026-01-01")])
    fields = {f["id"]: f for f in cg.build_overview()["quality"]["fields"]}
    assert fields["host"]["filled"] == 1 and fields["host"]["share"] == 25.0


def test_zpozdeni_jen_z_presnych_dat_odberu(ws):
    _ena(ws, [_run("A", collected="2020-01-01", public="2021-01-01"),
              _run("B", collected="2019", public="2021-01-01")],    # jen rok → nepočítá se
         [_taxon("klebsiella", runs=2, last_run="2026-01-01")])
    q = cg.build_overview()["quality"]
    assert q["lag_n"] == 1 and q["lag_median"] == 366
    assert q["date_precision"] == {"day": 1, "month": 0, "year": 1}


def test_prehled_rika_ze_pocty_nejsou_nemocnost(ws):
    _ena(ws, [_run()], [_taxon("klebsiella", runs=1, last_run="2026-01-01")])
    out = cg.build_overview()
    assert {"totals", "caveat_not_cases", "caveat_country"} <= set(out["facts"]["required"])
    text = out["comment"]["cs"]
    assert "není nemocnost" in text and "spodní odhad" in text
    assert len(text.split()) <= cg.Facts().max_words


# ── Linie ve stromech ────────────────────────────────────────────────────────

def test_linie_bez_dat_nespadnou(ws):
    assert cg.build_lineages() is None


def test_odkaz_na_strom_ma_predvybrane_cesko(ws):
    _trees(ws, [_tree("flu-h3n2", "seasonal-flu_h3n2_ha_2y", 7)], [("flu-h3n2", "K", "", 4)])
    tree = cg.build_lineages()["trees"][0]
    assert tree["url"] == "https://nextstrain.org/seasonal-flu/h3n2/ha/2y?f_country=Czech+Republic"
    assert tree["clades"] == [{"clade": "K", "count": 4}]


def test_ptaci_chripka_rika_ze_vzorky_nejsou_od_cloveka(ws):
    _trees(ws, [_tree("h5n1", "avian-flu_h5n1_ha_2y", 152, tips=5293, provenance="USDA; GISAID")],
           [("h5n1", "2.3.4.4b", "domestic", 108), ("h5n1", "2.3.4.4b", "wild", 13)])
    out = cg.build_lineages()
    assert "top_not_human" in out["facts"]["required"]
    assert "není od člověka" in out["comment"]["cs"]
    tree = out["trees"][0]
    # Linie se sčítá přes hostitele, hostitel je vlastní rozpad.
    assert tree["clades"] == [{"clade": "2.3.4.4b", "count": 121}]
    assert tree["hosts"] == [{"host": "domestic", "count": 108}, {"host": "wild", "count": 13}]


def test_strom_bez_ceskych_kmenu_se_vypise(ws):
    _trees(ws, [_tree("flu-h3n2", "seasonal-flu_h3n2_ha_2y", 7),
                _tree("measles", "measles_genome", 0), _tree("rsv-a", "rsv_a_genome_all-time", 0)],
           [("flu-h3n2", "K", "", 7)])
    out = cg.build_lineages()
    assert set(out["empty"]) == {"measles", "rsv-a"}
    assert "Spalničky" in out["comment"]["cs"]


def test_linie_nerikaji_zastoupeni_v_populaci(ws):
    _trees(ws, [_tree("sars-cov-2", "ncov_gisaid_europe_6m", 27)],
           [("sars-cov-2", "25A (LP.8.1)", "", 9)])
    out = cg.build_lineages()
    assert "caveat_small" in out["facts"]["required"]
    text = out["comment"]["cs"]
    assert "nelze usuzovat na zastoupení linií v populaci" in text
    assert len(text.split()) <= cg.Facts().max_words


# ── Rezistence ───────────────────────────────────────────────────────────────

def test_rezistence_bez_dat_nespadne(ws):
    assert cg.build_amr() is None


def test_geny_se_trideji_do_skupin(ws):
    _ncbi(ws, [_isolate(genes="blaNDM-1=COMPLETE,blaCTX-M-15=COMPLETE,emrD=COMPLETE"),
               _isolate(genes="blaOXA-48=COMPLETE"),
               _isolate(genes="blaCTX-M-15=COMPLETE"),
               _isolate(genes="fosA=COMPLETE")])
    groups = {g["id"]: g for g in cg.build_amr()["organisms"][0]["groups"]}
    assert groups["carbapenemase"]["isolates"] == 2
    assert groups["carbapenemase"]["genes"] == [{"gene": "blaNDM-1", "count": 1},
                                                {"gene": "blaOXA-48", "count": 1}]
    assert groups["esbl"]["isolates"] == 2


def test_skupina_genu_patri_jen_k_druhum_kde_dava_smysl(ws):
    _ncbi(ws, [_isolate("e-faecium", genes="vanB=COMPLETE")],
          releases={"e-faecium": {"organism": "Enterococcus_faecium", "release": "PDG000000071.978"}})
    groups = [g["id"] for g in cg.build_amr()["organisms"][0]["groups"]]
    assert groups == ["vancomycin"]            # karbapenemázy u enterokoka nedávají smysl


def test_vzorky_z_odpadnich_vod_se_pocitaji(ws):
    _ncbi(ws, [_isolate(source="urine"), _isolate(source="hospital wastewater"),
               _isolate(source="MWWTPs outlet"), _isolate(source="")])
    assert cg.build_amr()["organisms"][0]["environmental"] == 2


def test_rezistence_nepocita_procenta(ws):
    """Výběr izolátů není náhodný, podíl by vypadal jako výskyt rezistence u pacientů."""
    _ncbi(ws, [_isolate(genes="blaNDM-1=COMPLETE"), _isolate(genes="fosA=COMPLETE")])
    out = cg.build_amr()
    assert "caveat_not_share" in out["facts"]["required"]
    dumped = json.dumps(out, ensure_ascii=False)
    assert '"share' not in dumped and "procent" not in out["comment"]["cs"]
    assert "%" not in out["comment"]["cs"]
    # surové anglické názvy zdroje izolace na českou stránku nepatří
    assert "urine" not in dumped and "sources" not in dumped


def test_nadpis_karty_je_organismus_ne_nemoc(ws):
    _ncbi(ws, [_isolate("listeria", genes="fosX=COMPLETE")],
          releases={"listeria": {"organism": "Listeria", "release": "PDG000000001.4985"}})
    org = cg.build_amr()["organisms"][0]
    assert org["organism"] == "Listeria" and org["name"] == "Listerióza"


# ── Zakázaná slova a zápis ───────────────────────────────────────────────────

def test_komentare_neobsahuji_zakazana_slova(ws):
    _ena(ws, [_run()], [_taxon("klebsiella", runs=1, last_run="2026-01-01")])
    _trees(ws, [_tree("h5n1", "avian-flu_h5n1_ha_2y", 152)], [("h5n1", "2.3.4.4b", "domestic", 152)])
    _ncbi(ws, [_isolate(genes="blaNDM-1=COMPLETE")])
    for build in (cg.build_overview, cg.build_lineages, cg.build_amr):
        text = build()["comment"]["cs"]
        hit = FORBIDDEN.search(text)
        assert not hit, (text, hit.group(0) if hit else "")
        for word in ("nemocnost", "pacient"):          # smí padnout jen v záporu
            if word in text:
                assert any(n in text for n in ALLOWED_NEGATIONS), text


def test_main_zapise_tri_soubory(ws):
    _ena(ws, [_run()], [_taxon("klebsiella", runs=1, last_run="2026-01-01")])
    _trees(ws, [_tree("flu-h3n2", "seasonal-flu_h3n2_ha_2y", 7)], [("flu-h3n2", "K", "", 7)])
    _ncbi(ws, [_isolate(genes="blaNDM-1=COMPLETE")])
    assert cg.main() == 0
    for name in ("genomics_overview", "genomics_lineages", "genomics_amr"):
        obj = json.loads((ws / "out" / f"{name}.json").read_text(encoding="utf-8"))
        assert obj["generated_at"] and obj.get("meta", {}).get("chart_id") == name


# ── Datum aktualizace při výpadku zdroje ─────────────────────────────────────

def test_save_keeps_file_when_fetch_did_not_happen(ws):
    """Scraper selhal → CSV jsou z minula → JSON ani jeho generated_at se nemění."""
    cg.save("genomics_amr", {"generated_at": "2026-11-13T18:00:00Z",
                             "fetched_at": "2026-11-13T17:55:00Z", "organisms": []})
    path = ws / "out" / "genomics_amr.json"
    cg.save("genomics_amr", {"generated_at": "2026-11-20T18:00:00Z",
                             "fetched_at": "2026-11-13T17:55:00Z", "organisms": []})
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2026-11-13T18:00:00Z"


def test_save_rewrites_after_a_new_fetch_or_force(ws):
    first = {"generated_at": "2026-11-13T18:00:00Z", "fetched_at": "2026-11-13T17:55:00Z"}
    cg.save("genomics_amr", first)
    path = ws / "out" / "genomics_amr.json"

    cg.save("genomics_amr", {"generated_at": "2026-11-20T18:00:00Z",
                             "fetched_at": "2026-11-20T17:55:00Z"})
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2026-11-20T18:00:00Z"

    cg.save("genomics_amr", {"generated_at": "2026-11-21T09:00:00Z",
                             "fetched_at": "2026-11-20T17:55:00Z"}, force=True)
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2026-11-21T09:00:00Z"


def test_save_writes_when_fetch_time_is_unknown(ws):
    """Bez fetched_at se nedá poznat, že je vstup starý — raději zapsat než mlčky nic."""
    cg.save("genomics_lineages", {"generated_at": "2026-11-13T18:00:00Z", "fetched_at": None})
    cg.save("genomics_lineages", {"generated_at": "2026-11-20T18:00:00Z", "fetched_at": None})
    path = ws / "out" / "genomics_lineages.json"
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2026-11-20T18:00:00Z"


def test_acronym_keeps_its_capital_mid_sentence():
    assert cg._mid_sentence("Ptačí chřipka H5N1") == "ptačí chřipka H5N1"
    assert cg._mid_sentence("SARS-CoV-2 (Evropa)") == "SARS-CoV-2 (Evropa)"
    assert cg._mid_sentence("RSV typu A") == "RSV typu A"

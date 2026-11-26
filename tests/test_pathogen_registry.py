"""
Registr patogenů (curated/pathogens.yaml): drží pohromadě sám se sebou i se
zbytkem repozitáře, a kontrola opravdu chytí to, co by v databázi vypadalo
věrohodně a bylo špatně. Bez sítě a bez databáze.
"""
import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

import load_pathogens
import pathogen_registry as registry
import verify_pathogens

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def reg():
    return registry.read()


def _errors(reg, **kwargs):
    return registry.validate(reg, **kwargs)


def _broken(reg, pid, **changes):
    """Kopie registru s jedním pozměněným záznamem."""
    bad = copy.deepcopy(reg)
    record = next(p for p in bad["pathogens"] if p["id"] == pid)
    for key, value in changes.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return bad


# ── skutečný registr ─────────────────────────────────────────────────────────

def test_registr_projde_kontrolou(reg):
    assert _errors(reg) == []


def test_kazdy_patogen_ma_cesky_i_anglicky_nazev_a_taxon(reg):
    for p in reg["pathogens"]:
        assert p["name"]["cs"].strip() and p["name"]["en"].strip(), p["id"]
        assert isinstance(p["taxon"], int), p["id"]
        # Taxon jde do obou taxonomických schémat — to je „ID podle EMBL“.
        schemes = [s for s, _ in registry.identifiers(p)]
        assert schemes[:2] == ["ncbi-taxon", "ena-taxon"], p["id"]


def test_puvodni_radky_z_init_sql_jsou_v_registru(reg):
    """init.sql zakládá čtyři řádky; loader maže, co v registru není — nesmí je smazat."""
    sql = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
    seed = sql[sql.index("INSERT INTO pathogens"):]
    slugs = re.findall(r"^\s*\('([a-z0-9-]+)'", seed, flags=re.MULTILINE)
    assert len(slugs) == 4
    assert set(slugs) <= {p["id"] for p in reg["pathogens"]}


def test_kategorie_odpovidaji_omezeni_v_init_sql():
    sql = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
    check = re.search(r"category IN \(([^)]*)\)", sql).group(1)
    assert set(re.findall(r"'([a-z]+)'", check)) == registry.CATEGORIES


def test_druhy_terminu_a_stranek_odpovidaji_omezenim_v_init_sql(reg):
    sql = (ROOT / "db" / "init.sql").read_text(encoding="utf-8")
    kinds = set(re.findall(r"'([a-z]+)'", re.search(r"kind IN \(([^)]*)\)\),\s*\n\s*term", sql).group(1)))
    used = {kind for p in reg["pathogens"] for _, kind, _, _ in registry.terms(p)}
    assert used <= kinds
    dash = set(re.findall(r"'([a-z]+)'", re.search(r"kind IN \(('surveillance'[^)]*)\)", sql).group(1)))
    assert dash == registry.DASHBOARD_KINDS


def test_shoda_s_genomics_taxa(reg):
    """Týž patogen je i v genomics_taxa.yaml — stejné id musí znamenat stejný taxon a stránku."""
    by_id = {p["id"]: p for p in reg["pathogens"]}
    taxa = yaml.safe_load((ROOT / "curated" / "genomics_taxa.yaml").read_text(encoding="utf-8"))
    for t in taxa:
        p = by_id[t["id"]]
        assert p["taxon"] == t["taxon"], t["id"]
        assert "/dashboards/genomics/" in p["dashboards"], t["id"]
        if t["page"]:
            assert t["page"] in p["dashboards"], t["id"]


def test_ciselnik_diagnoz_ma_uredni_i_ctenarske_nazvy(reg):
    voc = registry.vocabulary(reg)
    # kód z dat ISIN: oba úřední názvy z labels_en.yaml
    assert voc["B05"]["name_cs"] == "Spalničky" and voc["B05"]["name_en"] == "Measles"
    # čtenářský název z diagnosis_names.yaml
    assert voc["A04"]["reader_cs"].startswith("Bakteriální střevní infekce")
    # kód mimo data: jen anglický název WHO, český úřední se nevymýšlí
    assert voc["U07.1"]["name_en"] == "COVID-19, virus identified"
    assert voc["U07.1"]["name_cs"] is None


def test_sdileny_kod_neni_nikdy_vylucny(reg):
    """A04 jsou „jiné bakteriální střevní infekce“ — nesmí být připsané jednomu patogenu."""
    by_id = {p["id"]: p for p in reg["pathogens"]}
    assert ("A04", True) in registry.diagnoses(by_id["campylobacter"])
    assert ("A04.5", False) in registry.diagnoses(by_id["campylobacter"])
    exact = [c for p in reg["pathogens"] for c, shared in registry.diagnoses(p) if not shared]
    assert "A04" not in exact and "A08" not in exact and "J10" not in exact


# ── kontrola chytá chyby ─────────────────────────────────────────────────────

@pytest.mark.parametrize("pid, changes, expected", [
    ("measles", {"category": "prion"}, "category"),
    ("measles", {"taxon": "11234"}, "taxon má být kladné celé číslo"),
    ("measles", {"taxon": 11320}, "taxon 11320 už má influenza-a"),
    ("measles", {"name": {"cs": "Virus spalniček"}}, "chybí name.en"),
    ("measles", {"disease": {"cs": "Spalničky"}}, "disease musí mít cs i en"),
    ("measles", {"icd10": ["B5"]}, "není kód MKN-10"),
    ("measles", {"icd10": ["Z99"]}, "kód Z99 není v labels_en.yaml"),
    ("measles", {"icd10": ["B05"], "icd10_shared": ["B05"]}, "je v icd10 i v icd10_shared"),
    ("measles", {"icd10": ["B05", "B26"]}, "kód B26 má jako výlučný už"),
    ("measles", {"dashboards": ["/dashboards/neexistuje/"]}, "není v oddílu dashboards"),
    ("measles", {"dashboards": None}, "dashboards má být seznam"),
    ("measles", {"parent": "measles"}, "parent"),
    ("measles", {"parent": "nikdo"}, "parent"),
    ("measles", {"ids": {"wikidata": "573943"}}, "nemá tvar identifikátoru"),
    ("measles", {"ids": {"gbif": "10245500"}}, "schéma není v oddílu schemes"),
    ("measles", {"ids": {"ena-taxon": "11234"}}, "plní se z pole taxon"),
    ("measles", {"verified": "2026-11-26"}, "verified má být datum"),
    ("measles", {"verified": date(2999, 1, 1)}, "je v budoucnosti"),
    ("measles", {"synonyms": {"de": ["Masern"]}}, "synonyms má být"),
])
def test_kontrola_chyti_chybny_zaznam(reg, pid, changes, expected):
    errors = _errors(_broken(reg, pid, **changes))
    assert any(expected in e for e in errors), errors


def test_kontrola_chyti_dvoji_id_a_spatne_id(reg):
    bad = copy.deepcopy(reg)
    bad["pathogens"].append(copy.deepcopy(bad["pathogens"][0]))
    assert any("je v registru dvakrát" in e for e in _errors(bad))
    bad = copy.deepcopy(reg)
    bad["pathogens"][0]["id"] = "SARS_CoV_2"
    assert any("jen malá písmena" in e for e in _errors(bad))


def test_kontrola_chyti_graf_ktery_pipeline_negeneruje(reg):
    bad = copy.deepcopy(reg)
    bad["dashboards"]["/dashboards/genomics/"]["chart"] = "genomics_neexistuje"
    assert any("není v charts.yaml" in e for e in _errors(bad))
    # skupinové grafy ISIN kryje jeden záznam `isin_group_*`
    assert registry.chart_is_known("isin_group_sti", {"isin_group_*"})
    assert not registry.chart_is_known("isin_groups_x", {"isin_group_*"})


def test_kontrola_chyti_rozchod_s_genomics_taxa(reg):
    taxa = [{"id": "measles", "taxon": 999, "page": "/dashboards/infectious-diseases-childhood/"},
            {"id": "neni-v-registru", "taxon": 1, "page": None}]
    errors = _errors(reg, genomics_taxa=taxa)
    assert any("taxon 11234 ≠ 999" in e for e in errors)
    assert any("neni-v-registru" in e for e in errors)


def test_kontrola_chyti_kod_zapsany_na_dvou_mistech(reg):
    """Kód, který už je v labels_en.yaml, nesmí mít druhý název v registru."""
    bad = copy.deepcopy(reg)
    bad["diagnoses"]["B05"] = {"en": "Measles"}
    assert any("už je v labels_en.yaml" in e for e in _errors(bad))


def test_load_vyhodi_vsechny_nalezy_najednou(tmp_path, reg):
    bad = _broken(_broken(reg, "measles", category="prion"), "mumps", taxon=-1)
    path = tmp_path / "pathogens.yaml"
    path.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(registry.RegistryError) as e:
        registry.load(path)
    assert "measles" in str(e.value) and "mumps" in str(e.value)


# ── řádky pro databázi ───────────────────────────────────────────────────────

def test_slovnicek_nese_zdroj_kazdeho_terminu(reg):
    p = next(p for p in reg["pathogens"] if p["id"] == "influenza-a")
    rows = registry.terms(p)
    assert ("la", "scientific", "Influenza A virus", "ncbi-taxonomy") in rows
    assert ("la", "species", "Alphainfluenzavirus influenzae", "ictv") in rows
    assert ("cs", "pathogen", "Virus chřipky A", "portal") in rows
    assert ("en", "disease", "Influenza A", "portal") in rows
    # primární klíč tabulky: (jazyk, druh, termín) se nesmí opakovat
    for p in reg["pathogens"]:
        keys = [r[:3] for r in registry.terms(p)]
        assert len(keys) == len(set(keys)), p["id"]


def test_otisk_zaznamu_se_meni_jen_se_zaznamem(reg):
    p = next(p for p in reg["pathogens"] if p["id"] == "tbe")
    assert registry.record_hash(p) == registry.record_hash(copy.deepcopy(p))
    assert registry.record_hash(p) == registry.record_hash(dict(reversed(list(p.items()))))
    for change in ({"verified": date(2027, 1, 1)}, {"dashboards": []}, {"icd10_shared": ["A84"]}):
        assert registry.record_hash({**p, **change}) != registry.record_hash(p)


def test_souhrn_pocita_radky_vsech_tabulek(reg):
    counts = load_pathogens.summary(reg, registry.vocabulary(reg))
    assert counts["pathogens"] == len(reg["pathogens"])
    # každý patogen má aspoň oba taxonomické identifikátory a vědecké + dva čtenářské názvy
    assert counts["pathogen_identifier"] >= 2 * counts["pathogens"]
    assert counts["pathogen_term"] >= 3 * counts["pathogens"]
    assert counts["dashboard"] == len(reg["dashboards"])


def test_dry_run_nepotrebuje_databazi(capsys):
    counts = load_pathogens.run(dry_run=True)
    assert counts["pathogens"] > 0
    assert "(dry-run)" in capsys.readouterr().out


def test_chyba_registru_nezastavi_nacitani_pozorovani(monkeypatch, capsys):
    """Překlep v registru nesmí zahodit načtená data: krok jen ohlásí chybu a vrátí False."""
    import load_to_db

    def boom(*args, **kwargs):
        raise registry.RegistryError("registr patogenů neprošel kontrolou")

    monkeypatch.setattr(load_pathogens, "run", boom)
    assert load_to_db.load_pathogen_registry(None, dry_run=True) is False
    assert "pokračuji bez něj" in capsys.readouterr().err


# ── čerstvost dat stránky ────────────────────────────────────────────────────

def _chart(tmp_path, name, obj):
    (tmp_path / f"{name}.json").write_text(json.dumps(obj), encoding="utf-8")
    return registry.chart_freshness(tmp_path, name)


def test_cerstvost_rozlisuje_kam_data_sahaji_a_kdy_byl_soubor_zapsan(tmp_path):
    through, generated = _chart(tmp_path, "flu_weekly", {
        "labels": ["KT 45/26"],
        "meta": {"generated_at": "2026-11-16T20:54:25Z", "period": {"end_date": "2026-11-08"}}})
    assert through == date(2026, 11, 8)
    assert generated == datetime(2026, 11, 16, 20, 54, 25, tzinfo=timezone.utc)


def test_cerstvost_cte_i_souhrn_a_rozmezi(tmp_path):
    assert _chart(tmp_path, "covid_summary", {"posledni_datum": "2026-11-25"})[0] == date(2026, 11, 25)
    assert _chart(tmp_path, "covid_by_vaccination",
                  {"obdobi": "2021-01-01 – 2026-11-25"})[0] == date(2026, 11, 25)
    # soubor s vlastním razítkem: přednost má to na první úrovni (to čte i portál)
    assert _chart(tmp_path, "flu_mem", {"generated_at": "2026-11-26T15:36:12Z",
                                        "meta": {"generated_at": "2026-11-26T15:36:15Z"}})[1].second == 12


def test_cerstvost_bez_souboru_nebo_s_rozbitym_souborem_neni_chyba(tmp_path):
    assert registry.chart_freshness(tmp_path, "neexistuje") == (None, None)
    assert registry.chart_freshness(tmp_path, None) == (None, None)
    (tmp_path / "rozbity.json").write_text("{", encoding="utf-8")
    assert registry.chart_freshness(tmp_path, "rozbity") == (None, None)
    assert _chart(tmp_path, "bez_data", {"labels": ["80+"]}) == (None, None)


# ── ověření proti autoritám (jen čisté funkce, bez sítě) ─────────────────────

ENA_FLU = {"scientificName": "Influenza A virus", "rank": "no rank",
           "lineage": "Viruses; Orthomyxoviridae; Alphainfluenzavirus; Alphainfluenzavirus influenzae; "}


def test_overeni_taxonu_sedi_a_hlasi_prejmenovani(reg):
    p = next(p for p in reg["pathogens"] if p["id"] == "influenza-a")
    assert verify_pathogens.check_taxon(p, ENA_FLU) == []
    renamed = {**ENA_FLU, "scientificName": "Alphainfluenzavirus A",
               "lineage": "Viruses; Orthomyxoviridae; Alphainfluenzavirus; "}
    found = verify_pathogens.check_taxon(p, renamed)
    assert any("scientific_name" in f for f in found)
    assert any("species" in f for f in found)
    assert verify_pathogens.check_taxon(p, None) == ["influenza-a: taxon 11320 v ENA taxonomii není"]


def test_overeni_wikidat(reg):
    p = next(p for p in reg["pathogens"] if p["id"] == "influenza-a")
    assert verify_pathogens.check_wikidata(p, {"Q834390": {"mesh": ["D009980"]}}) == []
    assert "nemá NCBI taxonomy ID" in verify_pathogens.check_wikidata(p, {"Q1": {"mesh": []}})[0]
    assert "MeSH D009980 není" in verify_pathogens.check_wikidata(p, {"Q834390": {"mesh": []}})[0]
    # záznam bez Wikidat: nález jen tehdy, když Wikidata položku mezitím získala
    q = {**p, "ids": {}}
    assert verify_pathogens.check_wikidata(q, {}) == []
    assert "doplnit ids.wikidata" in verify_pathogens.check_wikidata(q, {"Q834390": {"mesh": []}})[0]

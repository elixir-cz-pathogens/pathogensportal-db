-- Pathogen Portal CZ — initial schema
-- Extend this file as the data pipeline grows.

CREATE TABLE IF NOT EXISTS pathogens (
    id              SERIAL PRIMARY KEY,
    slug            VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    scientific_name VARCHAR(255),
    category        VARCHAR(100) CHECK (category IN ('virus', 'bacteria', 'fungus', 'parasite', 'other')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_data (
    id             SERIAL PRIMARY KEY,
    dashboard_slug VARCHAR(255) NOT NULL,
    data_key       VARCHAR(255) NOT NULL,
    data_value     JSONB,
    recorded_at    TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (dashboard_slug, data_key, recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_dashboard_data_slug     ON dashboard_data (dashboard_slug);
CREATE INDEX IF NOT EXISTS idx_dashboard_data_recorded ON dashboard_data (recorded_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- Analytická vrstva: normalizovaná pozorování ze všech zdrojů v jednom tvaru.
--
-- Všechny naše zdroje (ISIN, MZČR, SZÚ) měří v zásadě totéž: "kolik případů
-- něčeho bylo za nějaké období, na nějakém území, v nějaké skupině". Proto jedna
-- tabulka, ne tabulka na zdroj — dotazy napříč zdroji pak nepotřebují UNION.
--
-- snapshot_date je v UNIQUE klíči schválně: pipeline nepřepisuje, ale přidává
-- novou verzi téhož pozorování. Tím vzniká reporting triangle (jak se čísla za
-- dané období postupně doplňují dodatečnými hlášeními), bez kterého nejde
-- spočítat nowcasting — korekce reportovacího zpoždění.
CREATE TABLE IF NOT EXISTS observation (
    id             BIGSERIAL PRIMARY KEY,
    source_id      VARCHAR(64)  NOT NULL,   -- 'isin' | 'mzcr' | 'szu' | ...
    diagnosis_code VARCHAR(32),             -- MKN-10, kde dává smysl
    diagnosis_name VARCHAR(255),
    region_code    VARCHAR(16),             -- NUTS3; NULL = celá ČR
    age_group      VARCHAR(64),             -- NULL = všechny věky
    sex            VARCHAR(16),             -- NULL = obě pohlaví
    period_start   DATE         NOT NULL,
    period_end     DATE         NOT NULL,
    metric         VARCHAR(64)  NOT NULL,   -- cases | deaths | tests | hospitalizations | lab_detections
                                            -- | population_covered | rate_per_100k
    value          NUMERIC      NOT NULL,
    snapshot_date  DATE         NOT NULL,
    ingested_at    TIMESTAMPTZ  DEFAULT NOW(),
    -- NULLS NOT DISTINCT je tu nutnost, ne kosmetika: NULL znamená "nerozlišeno
    -- podle téhle dimenze" (ISIN neagreguje podle pohlaví, takže sex je vždy NULL).
    -- Ve výchozím chování Postgresu se dva NULL nepovažují za shodné, unikátní klíč
    -- by tedy nikdy nesedl, ON CONFLICT by se nespustil a každý běh loaderu by
    -- data zduplikoval. Vyžaduje PostgreSQL 15+ (compose pinuje postgres:16).
    UNIQUE NULLS NOT DISTINCT (source_id, diagnosis_name, region_code, age_group, sex,
                               period_start, metric, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_observation_lookup
    ON observation (source_id, metric, period_start);
CREATE INDEX IF NOT EXISTS idx_observation_region
    ON observation (region_code, period_start);
CREATE INDEX IF NOT EXISTS idx_observation_diagnosis
    ON observation (diagnosis_name);

-- Jmenovatele (ČSÚ). Bez nich jsou to počty, ne incidence.
CREATE TABLE IF NOT EXISTS population (
    region_code VARCHAR(16) NOT NULL,       -- NUTS3; 'CZ' = celá republika
    region_name VARCHAR(128),
    age_group   VARCHAR(64) NOT NULL DEFAULT 'total',
    sex         VARCHAR(16) NOT NULL DEFAULT 'total',
    year        INT         NOT NULL,
    value       BIGINT      NOT NULL,
    PRIMARY KEY (region_code, age_group, sex, year)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Informační vrstva: registr patogenů.
--
-- Zdrojem pravdy je curated/pathogens.yaml; tyhle tabulky jsou jeho kopie, kterou
-- scripts/load_pathogens.py kdykoli postaví znovu. Ručně se tu nic neupravuje —
-- příští běh loaderu by změnu přepsal. Popis: docs/pathogen-registry.md.
--
-- Tabulka `pathogens` vznikla dřív (čtyři ukázkové řádky níž) a může už existovat
-- v běžící databázi. Proto se rozšiřuje přes ADD COLUMN IF NOT EXISTS, ne novým
-- CREATE TABLE — ten by na existující tabulce neudělal nic a sloupce by chyběly.
-- Sloupec `name` zůstává kvůli zpětné kompatibilitě a nese anglický název.
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS name_cs      VARCHAR(255);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS name_en      VARCHAR(255);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS disease_cs   VARCHAR(255);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS disease_en   VARCHAR(255);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS taxon_id     INTEGER;       -- NCBI Taxonomy = ENA (EMBL-EBI)
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS taxon_rank   VARCHAR(64);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS taxon_note   TEXT;          -- proč taxon nesedí přesně
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS species      VARCHAR(255);  -- binomické jméno druhu podle ICTV
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS family       VARCHAR(128);
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS parent_slug  VARCHAR(255);  -- nadřazený patogen (subtyp → typ)
-- Kdy člověk naposledy porovnal záznam se zdroji (pole `verified` v registru).
-- Neplést s updated_at: to říká, kdy se záznam naposledy ZMĚNIL v téhle databázi.
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS verified_on  DATE;
-- Otisk záznamu v registru. Loader podle něj pozná, že se nic nezměnilo, a
-- updated_at nechá být — jinak by každý běh hlásil „aktualizováno dnes“.
ALTER TABLE pathogens ADD COLUMN IF NOT EXISTS record_hash  CHAR(64);

CREATE INDEX IF NOT EXISTS idx_pathogens_taxon ON pathogens (taxon_id);

-- Číselník schémat identifikátorů (NCBI/ENA taxon, Wikidata, MeSH…).
-- uri_template obsahuje {id}; odkaz na záznam skládá pohled pathogen_ids, takže
-- změna adresy u autority znamená jeden řádek tady, ne přepis všech identifikátorů.
CREATE TABLE IF NOT EXISTS identifier_scheme (
    scheme       VARCHAR(32)  PRIMARY KEY,
    title        VARCHAR(255) NOT NULL,
    authority    VARCHAR(255) NOT NULL,
    homepage     TEXT,
    uri_template TEXT
);

CREATE TABLE IF NOT EXISTS pathogen_identifier (
    pathogen_id INTEGER     NOT NULL REFERENCES pathogens (id) ON DELETE CASCADE,
    scheme      VARCHAR(32) NOT NULL REFERENCES identifier_scheme (scheme),
    value       VARCHAR(64) NOT NULL,
    PRIMARY KEY (pathogen_id, scheme, value)
);

CREATE INDEX IF NOT EXISTS idx_pathogen_identifier_value ON pathogen_identifier (scheme, value);

-- Slovníček: všechna jména, pod kterými se patogen dá najít, česky, anglicky a
-- latinsky. `source` říká, čí je to slovo — vědecké jméno z taxonomie není totéž
-- co název, který portál zvolil pro čtenáře.
CREATE TABLE IF NOT EXISTS pathogen_term (
    pathogen_id INTEGER      NOT NULL REFERENCES pathogens (id) ON DELETE CASCADE,
    lang        VARCHAR(8)   NOT NULL CHECK (lang IN ('cs', 'en', 'la')),
    kind        VARCHAR(16)  NOT NULL CHECK (kind IN ('scientific', 'species', 'pathogen', 'disease',
                                                      'synonym', 'abbreviation')),
    term        VARCHAR(255) NOT NULL,
    source      VARCHAR(32)  NOT NULL,   -- ncbi-taxonomy | ictv | portal
    PRIMARY KEY (pathogen_id, lang, kind, term)
);

CREATE INDEX IF NOT EXISTS idx_pathogen_term_lookup ON pathogen_term (lower(term));

-- Číselník diagnóz MKN-10: oficiální názvy (česky ÚZIS, anglicky WHO ICD-10 2019)
-- a vedle nich čtenářské názvy portálu. NULL = název v repozitáři není; raději
-- mezera než vlastní překlad, který by vypadal úředně.
-- `code` je stejný kód jako observation.diagnosis_code, takže se registr dá
-- spojit s pozorováními.
CREATE TABLE IF NOT EXISTS diagnosis (
    code      VARCHAR(32)  PRIMARY KEY,
    name_cs   VARCHAR(255),
    name_en   VARCHAR(255),
    reader_cs VARCHAR(255),
    reader_en VARCHAR(255)
);

-- shared = TRUE: pod kódem jsou i jiní původci (A04 „jiné bakteriální střevní
-- infekce“ je kampylobakter a další). Počty pod sdíleným kódem se NESMÍ připsat
-- jednomu patogenu — dotaz, který to potřebuje, filtruje NOT shared.
CREATE TABLE IF NOT EXISTS pathogen_diagnosis (
    pathogen_id INTEGER     NOT NULL REFERENCES pathogens (id) ON DELETE CASCADE,
    code        VARCHAR(32) NOT NULL REFERENCES diagnosis (code),
    shared      BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (pathogen_id, code)
);

CREATE INDEX IF NOT EXISTS idx_pathogen_diagnosis_code ON pathogen_diagnosis (code);

-- Stránky portálu. `chart` je graf, který nese datum dat stránky (v portálu
-- `update_from`); z něj loader při každém běhu přečte data_through (kam data
-- sahají) a data_generated_at (kdy pipeline soubor naposledy zapsala). Dvě různá
-- tvrzení — nesmí splynout do jednoho „aktualizováno“.
CREATE TABLE IF NOT EXISTS dashboard (
    path              VARCHAR(255) PRIMARY KEY,   -- cesta české stránky; anglická je táž pod /en
    kind              VARCHAR(32)  NOT NULL CHECK (kind IN ('surveillance', 'genomics', 'report', 'external')),
    chart             VARCHAR(128),
    data_through      DATE,
    data_generated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pathogen_dashboard (
    pathogen_id    INTEGER      NOT NULL REFERENCES pathogens (id) ON DELETE CASCADE,
    dashboard_path VARCHAR(255) NOT NULL REFERENCES dashboard (path) ON DELETE CASCADE,
    PRIMARY KEY (pathogen_id, dashboard_path)
);

-- Pohledy se zahazují a zakládají znovu: CREATE OR REPLACE neumí sloupec ubrat
-- ani přejmenovat, takže by první úprava pohledu shodila celý init.sql.
DROP VIEW IF EXISTS pathogen_overview;
DROP VIEW IF EXISTS pathogen_glossary;
DROP VIEW IF EXISTS pathogen_ids;

-- Jeden řádek na patogen: jména, taxon, kódy, stránky a čerstvost dat.
CREATE VIEW pathogen_overview AS
SELECT p.slug,
       p.category,
       p.name_cs,
       p.name_en,
       p.disease_cs,
       p.disease_en,
       p.scientific_name,
       p.species,
       p.family,
       p.taxon_id,
       p.taxon_rank,
       p.parent_slug,
       (SELECT array_agg(d.code ORDER BY d.code)
          FROM pathogen_diagnosis d WHERE d.pathogen_id = p.id AND NOT d.shared)  AS icd10,
       (SELECT array_agg(d.code ORDER BY d.code)
          FROM pathogen_diagnosis d WHERE d.pathogen_id = p.id AND d.shared)      AS icd10_shared,
       (SELECT array_agg(pd.dashboard_path ORDER BY pd.dashboard_path)
          FROM pathogen_dashboard pd WHERE pd.pathogen_id = p.id)                 AS dashboards,
       (SELECT max(b.data_through)
          FROM pathogen_dashboard pd JOIN dashboard b ON b.path = pd.dashboard_path
         WHERE pd.pathogen_id = p.id)                                             AS data_through,
       (SELECT max(b.data_generated_at)
          FROM pathogen_dashboard pd JOIN dashboard b ON b.path = pd.dashboard_path
         WHERE pd.pathogen_id = p.id)                                             AS data_generated_at,
       p.verified_on,
       p.updated_at
  FROM pathogens p;

-- Slovníček v jednom pohledu: vlastní termíny registru a k nim oficiální názvy
-- diagnóz, které patří jen tomuto patogenu (sdílené kódy by jméno připsaly neprávem).
CREATE VIEW pathogen_glossary AS
SELECT p.slug, t.lang, t.kind, t.term, t.source, NULL::VARCHAR AS code
  FROM pathogen_term t JOIN pathogens p ON p.id = t.pathogen_id
UNION ALL
SELECT p.slug, 'cs', 'diagnosis', g.name_cs, 'mkn-10', g.code
  FROM pathogen_diagnosis d
  JOIN diagnosis g ON g.code = d.code
  JOIN pathogens p ON p.id = d.pathogen_id
 WHERE NOT d.shared AND g.name_cs IS NOT NULL
UNION ALL
SELECT p.slug, 'en', 'diagnosis', g.name_en, 'icd-10-who', g.code
  FROM pathogen_diagnosis d
  JOIN diagnosis g ON g.code = d.code
  JOIN pathogens p ON p.id = d.pathogen_id
 WHERE NOT d.shared AND g.name_en IS NOT NULL;

-- Identifikátory i s odkazem na záznam u autority.
CREATE VIEW pathogen_ids AS
SELECT p.slug, i.scheme, s.title AS scheme_title, s.authority, i.value,
       replace(s.uri_template, '{id}', i.value) AS uri
  FROM pathogen_identifier i
  JOIN identifier_scheme s ON s.scheme = i.scheme
  JOIN pathogens p ON p.id = i.pathogen_id;

-- Čtyři řádky z doby před registrem. Zůstávají, aby čerstvě založená databáze
-- nebyla prázdná, dokud nad ní neproběhne loader; ten je doplní z registru
-- (všechny čtyři v něm jsou — hlídá to tests/test_pathogen_registry.py).
INSERT INTO pathogens (slug, name, scientific_name, category) VALUES
    ('sars-cov-2',  'SARS-CoV-2',  'Severe acute respiratory syndrome coronavirus 2', 'virus'),
    ('influenza-a', 'Influenza A', 'Influenza A virus',                               'virus'),
    ('influenza-b', 'Influenza B', 'Influenza B virus',                               'virus'),
    ('poliovirus',  'Poliovirus',  'Enterovirus C',                                   'virus')
ON CONFLICT (slug) DO NOTHING;

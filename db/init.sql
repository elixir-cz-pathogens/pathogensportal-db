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

INSERT INTO pathogens (slug, name, scientific_name, category) VALUES
    ('sars-cov-2',  'SARS-CoV-2',  'Severe acute respiratory syndrome coronavirus 2', 'virus'),
    ('influenza-a', 'Influenza A', 'Influenza A virus',                               'virus'),
    ('influenza-b', 'Influenza B', 'Influenza B virus',                               'virus'),
    ('poliovirus',  'Poliovirus',  'Enterovirus C',                                   'virus')
ON CONFLICT (slug) DO NOTHING;

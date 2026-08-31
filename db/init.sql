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

INSERT INTO pathogens (slug, name, scientific_name, category) VALUES
    ('sars-cov-2',  'SARS-CoV-2',  'Severe acute respiratory syndrome coronavirus 2', 'virus'),
    ('influenza-a', 'Influenza A', 'Influenza A virus',                               'virus'),
    ('influenza-b', 'Influenza B', 'Influenza B virus',                               'virus'),
    ('poliovirus',  'Poliovirus',  'Enterovirus C',                                   'virus')
ON CONFLICT (slug) DO NOTHING;

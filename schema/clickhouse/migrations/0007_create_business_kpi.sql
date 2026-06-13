-- HELIOS V3 — migration 0007 — business_kpi
-- Time-series of business metrics (revenue, conversions, support resolution, CSAT...)
-- ingested from the business side. Correlated against agent runtime telemetry to
-- answer the executive question: "did this technical issue hurt the business?"

CREATE TABLE IF NOT EXISTS helios.business_kpi
(
    -- envelope
    schema_version   LowCardinality(String) DEFAULT '1.0.0',
    tenant_id        LowCardinality(String) DEFAULT 'default',

    -- identity
    kpi_id           UUID,
    metric           LowCardinality(String),   -- e.g. 'support_resolution_rate'
    unit             LowCardinality(String) DEFAULT '',  -- 'percent' | 'usd' | 'count'

    -- value at a point in time (a bucketed window)
    bucket_start     DateTime64(3, 'UTC'),
    value            Float64,
    segment          LowCardinality(String) DEFAULT 'all',  -- e.g. product/region/team

    -- bookkeeping
    ingested_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(bucket_start)
ORDER BY (tenant_id, metric, segment, bucket_start)
TTL toDateTime(bucket_start) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

-- HELIOS V3 — migration 0008 — outcome_correlation
-- Stores the result of correlating a business-KPI shift with agent-runtime
-- telemetry in the same window: the quantified, human-readable link such as
-- "support resolution dropped 14% because retriever latency rose after the
-- vector DB upgrade."

CREATE TABLE IF NOT EXISTS helios.outcome_correlation
(
    -- envelope
    schema_version    LowCardinality(String) DEFAULT '1.0.0',
    tenant_id         LowCardinality(String) DEFAULT 'default',

    -- identity
    correlation_id    UUID,
    metric            LowCardinality(String),
    segment           LowCardinality(String) DEFAULT 'all',

    -- the KPI shift being explained
    window_start      DateTime64(3, 'UTC'),
    window_end        DateTime64(3, 'UTC'),
    baseline_value    Float64,
    current_value     Float64,
    delta_pct         Float64,            -- signed % change vs baseline
    direction         LowCardinality(String),  -- 'drop' | 'rise' | 'flat'

    -- the technical driver found in telemetry
    driver_metric     LowCardinality(String) DEFAULT '',  -- e.g. 'mcp_error_rate'
    driver_value      Float64 DEFAULT 0,
    driver_baseline   Float64 DEFAULT 0,
    correlation_score Float32 DEFAULT 0,  -- 0..1 strength of the link

    -- the narrative
    summary           String DEFAULT '',
    -- JSON array of contributing factors [{factor, weight, evidence}]
    factors           String DEFAULT '[]',
    reasoner          LowCardinality(String) DEFAULT 'heuristic',
    model             String DEFAULT '',

    -- bookkeeping
    created_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(window_start)
ORDER BY (tenant_id, metric, window_start)
TTL toDateTime(window_start) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

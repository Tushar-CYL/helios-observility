-- HELIOS V1 — migration 0003 — memory_operation
-- read/write/update/evict against a memory backend. Captured, not managed.
-- Staleness signals (created_at, age_ms, is_stale) drive the V1 demo.

CREATE TABLE IF NOT EXISTS helios.memory_operation
(
    -- envelope
    schema_version      LowCardinality(String),
    tenant_id           LowCardinality(String) DEFAULT 'default',
    agent_run_id        UUID,

    -- identity / trace linkage
    memory_operation_id UUID,
    trace_id            String,
    span_id             String,

    -- operation + backend (backend is opaque to HELIOS)
    op                  LowCardinality(String),
    store               LowCardinality(String) DEFAULT '',
    namespace           LowCardinality(String) DEFAULT '',
    key                 String DEFAULT '',
    query               String DEFAULT '',
    content             String DEFAULT '',

    -- provenance
    source              LowCardinality(String) DEFAULT '',
    confidence          Nullable(Float32),

    -- staleness
    created_at          Nullable(DateTime64(9, 'UTC')),
    age_ms              Nullable(UInt64),
    is_stale            Nullable(UInt8),

    -- result
    latency_ms          UInt32 DEFAULT 0,
    status              LowCardinality(String),

    -- bookkeeping
    event_time          DateTime64(9, 'UTC') DEFAULT now64(9),
    ingested_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_id, agent_run_id, span_id)
TTL toDateTime(event_time) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

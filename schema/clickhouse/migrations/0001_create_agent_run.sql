-- HELIOS V1 — migration 0001 — agent_run
-- One logical agent execution (root span). Correlation root via agent_run_id.

CREATE TABLE IF NOT EXISTS helios.agent_run
(
    -- envelope
    schema_version   LowCardinality(String),
    tenant_id        LowCardinality(String) DEFAULT 'default',
    agent_run_id     UUID,

    -- trace linkage
    trace_id         String,
    root_span_id     String,

    -- identity
    agent_name       LowCardinality(String),
    agent_id         String DEFAULT '',
    agent_version    LowCardinality(String) DEFAULT '',
    agent_framework  LowCardinality(String) DEFAULT 'custom',

    -- task (opt-in payloads; redacted at ingestion)
    input            String DEFAULT '',
    output           String DEFAULT '',

    -- lifecycle
    status           LowCardinality(String),
    start_time       DateTime64(9, 'UTC'),
    end_time         DateTime64(9, 'UTC'),
    duration_ms      UInt32,

    -- usage (low-effort enrichment)
    input_tokens     UInt32 DEFAULT 0,
    output_tokens    UInt32 DEFAULT 0,
    cost_usd         Float64 DEFAULT 0,

    -- outcome (eval/feedback)
    outcome_correct  Nullable(UInt8),

    -- bookkeeping
    ingested_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(start_time)
ORDER BY (tenant_id, start_time, agent_run_id)
TTL toDateTime(start_time) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

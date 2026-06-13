-- HELIOS V1 — migration 0004 — decision_edge
-- The causal backbone: which source (memory|tool|llm) influenced which target.
-- Reconstructs the path: stale memory -> llm_call -> wrong final_answer.

CREATE TABLE IF NOT EXISTS helios.decision_edge
(
    -- envelope
    schema_version   LowCardinality(String),
    tenant_id        LowCardinality(String) DEFAULT 'default',
    agent_run_id     UUID,

    -- identity / trace linkage
    decision_edge_id UUID,
    trace_id         String DEFAULT '',

    -- edge
    source_type      LowCardinality(String),
    source_id        String,
    target_type      LowCardinality(String),
    target_id        String,
    relation         LowCardinality(String),
    weight           Nullable(Float32),
    step_index       UInt32 DEFAULT 0,

    -- bookkeeping
    event_time       DateTime64(9, 'UTC') DEFAULT now64(9),
    ingested_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_id, agent_run_id, step_index, decision_edge_id)
TTL toDateTime(event_time) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

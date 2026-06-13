-- HELIOS V2 — migration 0005 — rca_report
-- Stores the AI Ops Copilot / RCA output for an agent run: a ranked set of
-- grounded root-cause hypotheses, a suggested fix, and the evidence used.
-- Written by the RCA service; read by the Grafana flagship dashboard.

CREATE TABLE IF NOT EXISTS helios.rca_report
(
    -- envelope
    schema_version   LowCardinality(String),
    tenant_id        LowCardinality(String) DEFAULT 'default',
    agent_run_id     UUID,

    -- identity
    report_id        UUID,
    trigger          LowCardinality(String) DEFAULT 'on_demand',

    -- result
    summary          String,
    -- JSON array: [{ "hypothesis", "probability", "evidence" }, ...]
    causes           String DEFAULT '[]',
    suggested_fix    String DEFAULT '',
    confidence       Float32 DEFAULT 0,

    -- provenance
    reasoner         LowCardinality(String) DEFAULT 'heuristic',
    model            String DEFAULT '',
    -- JSON array of evidence references (trace/span/entity ids)
    evidence_refs    String DEFAULT '[]',
    latency_ms       UInt32 DEFAULT 0,

    -- bookkeeping
    created_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (tenant_id, agent_run_id, created_at)
TTL toDateTime(created_at) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

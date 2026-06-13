-- HELIOS V2 — migration 0006 — eval_result
-- Stores evaluation/quality scores for an agent run: code/heuristic checks,
-- LLM-as-judge verdicts, and drift/hallucination signals. Written by the eval
-- engine; read by the Grafana eval panels and consumed by the RCA Copilot.

CREATE TABLE IF NOT EXISTS helios.eval_result
(
    -- envelope
    schema_version   LowCardinality(String),
    tenant_id        LowCardinality(String) DEFAULT 'default',
    agent_run_id     UUID,

    -- identity
    eval_result_id   UUID,
    evaluator        LowCardinality(String),   -- e.g. 'answer_grounded', 'llm_judge'
    evaluator_kind   LowCardinality(String),   -- 'code' | 'llm_judge' | 'drift'

    -- verdict
    passed           UInt8,                    -- 1 pass / 0 fail
    score            Float32,                  -- 0..1
    label            LowCardinality(String) DEFAULT '',  -- e.g. 'grounded'/'hallucinated'
    reason           String DEFAULT '',

    -- provenance
    reasoner         LowCardinality(String) DEFAULT 'heuristic',
    model            String DEFAULT '',
    latency_ms       UInt32 DEFAULT 0,

    -- bookkeeping
    created_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (tenant_id, agent_run_id, evaluator, created_at)
TTL toDateTime(created_at) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

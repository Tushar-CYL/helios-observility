-- HELIOS V1 — migration 0002 — mcp_invocation
-- A single call to an MCP server. Captured via versioned adapters
-- (mcp.protocol.version), never hard-coded to one MCP shape.

CREATE TABLE IF NOT EXISTS helios.mcp_invocation
(
    -- envelope
    schema_version    LowCardinality(String),
    tenant_id         LowCardinality(String) DEFAULT 'default',
    agent_run_id      UUID,

    -- identity / trace linkage
    mcp_invocation_id UUID,
    trace_id          String,
    span_id           String,
    parent_span_id    String DEFAULT '',

    -- server + protocol
    server_name       LowCardinality(String),
    server_version    LowCardinality(String) DEFAULT '',
    transport         LowCardinality(String) DEFAULT '',
    protocol_version  LowCardinality(String),

    -- operation
    method            LowCardinality(String),
    tool_name         LowCardinality(String) DEFAULT '',
    tool_arguments    String DEFAULT '',
    tool_result       String DEFAULT '',

    -- permissions
    permission_scope  String DEFAULT '',
    permission_changed UInt8 DEFAULT 0,

    -- ordering within the run's MCP chain
    sequence_index    UInt32 DEFAULT 0,

    -- result
    latency_ms        UInt32,
    status            LowCardinality(String),
    error_type        LowCardinality(String) DEFAULT '',
    error_message     String DEFAULT '',
    failure_mode      LowCardinality(String) DEFAULT '',

    -- bookkeeping
    event_time        DateTime64(9, 'UTC') DEFAULT now64(9),
    ingested_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_id, agent_run_id, sequence_index, span_id)
TTL toDateTime(event_time) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

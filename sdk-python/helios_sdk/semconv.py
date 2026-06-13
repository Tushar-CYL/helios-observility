"""HELIOS semantic conventions (v1.0.0) — attribute keys and enums.

Single source of truth for instrumentation, mirroring
``schema/semconv/helios-semconv.md``. Importing constants from here (instead of
hard-coding strings) keeps the SDK, the storage schema, and the spec in lockstep.
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
DEFAULT_TENANT = "default"

# Routing attribute: tells the HELIOS span processor which table a span maps to.
ENTITY = "helios.entity"

# Entity values.
ENTITY_AGENT_RUN = "agent_run"
ENTITY_MCP = "mcp_invocation"
ENTITY_MEMORY = "memory_operation"
ENTITY_DECISION = "decision_edge"

# Envelope (present on every record).
SCHEMA_VERSION_KEY = "helios.schema_version"
TENANT_ID = "helios.tenant_id"
AGENT_RUN_ID = "helios.agent_run_id"

# agent_run.
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
AGENT_VERSION = "helios.agent.version"
AGENT_FRAMEWORK = "helios.agent.framework"
AGENT_INPUT = "helios.agent.input"
AGENT_OUTPUT = "helios.agent.output"
AGENT_STATUS = "helios.agent.status"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
USAGE_COST_USD = "helios.usage.cost_usd"
OUTCOME_CORRECT = "helios.outcome.correct"

# Generic status / error (shared by mcp + memory).
STATUS = "helios.status"
ERROR_TYPE = "helios.error.type"
ERROR_MESSAGE = "helios.error.message"

# mcp_invocation.
MCP_INVOCATION_ID = "helios.mcp_invocation_id"
MCP_SERVER_NAME = "mcp.server.name"
MCP_SERVER_VERSION = "mcp.server.version"
MCP_TRANSPORT = "mcp.transport"
MCP_PROTOCOL_VERSION = "mcp.protocol.version"
MCP_METHOD = "mcp.method"
MCP_TOOL_NAME = "mcp.tool.name"
MCP_TOOL_ARGUMENTS = "mcp.tool.arguments"
MCP_TOOL_RESULT = "mcp.tool.result"
MCP_PERMISSION_SCOPE = "mcp.permission.scope"
MCP_PERMISSION_CHANGED = "mcp.permission.changed"
MCP_SEQUENCE_INDEX = "mcp.sequence_index"
MCP_FAILURE_MODE = "mcp.failure_mode"

# memory_operation.
MEMORY_OPERATION_ID = "helios.memory_operation_id"
MEMORY_OP = "memory.op"
MEMORY_STORE = "memory.store"
MEMORY_NAMESPACE = "memory.namespace"
MEMORY_KEY = "memory.key"
MEMORY_QUERY = "memory.query"
MEMORY_CONTENT = "memory.content"
MEMORY_SOURCE = "memory.source"
MEMORY_CONFIDENCE = "memory.confidence"
MEMORY_CREATED_AT = "memory.created_at"
MEMORY_AGE_MS = "memory.age_ms"
MEMORY_IS_STALE = "memory.is_stale"

# decision_edge.
DECISION_EDGE_ID = "helios.decision_edge_id"
DECISION_SOURCE_TYPE = "decision.source.type"
DECISION_SOURCE_ID = "decision.source.id"
DECISION_TARGET_TYPE = "decision.target.type"
DECISION_TARGET_ID = "decision.target.id"
DECISION_RELATION = "decision.relation"
DECISION_WEIGHT = "decision.weight"
DECISION_STEP_INDEX = "decision.step_index"

# --- Enumerations (validated by the API layer) ---
FRAMEWORKS = frozenset({"langgraph", "autogen", "crewai", "custom"})
STATUSES = frozenset({"ok", "error"})
TRANSPORTS = frozenset({"stdio", "http", "sse"})
FAILURE_MODES = frozenset(
    {"timeout", "permission_denied", "server_error", "bad_response", "cancelled"}
)
MEMORY_OPS = frozenset({"read", "write", "update", "evict"})
DECISION_SOURCE_TYPES = frozenset({"memory_operation", "mcp_invocation", "llm_call"})
DECISION_TARGET_TYPES = frozenset(
    {"mcp_invocation", "memory_operation", "llm_call", "final_answer", "agent_step"}
)
DECISION_RELATIONS = frozenset({"influenced", "caused", "informed", "triggered"})

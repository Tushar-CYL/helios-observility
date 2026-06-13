"""HELIOS public instrumentation API.

Ergonomic context managers that create OpenTelemetry spans carrying the HELIOS
semantic conventions. Correlation (``agent_run_id``, sequence/step indices,
parent spans) is wired automatically via a context variable.

    import helios_sdk as helios

    helios.init(service_name="support-assistant")

    with helios.agent_run("support-assistant", framework="langgraph",
                          input="What is the refund window?") as run:
        with helios.mcp_call("policy-db-mcp", "fetch_policy",
                             protocol_version="2025-06-18") as call:
            ...                       # call.fail(...) to mark a failure
        with helios.memory_op("read", store="redis", key="refund-window",
                              created_at=cached_at, stale_after_seconds=86400) as mem:
            ...
        helios.decision(call, mem, "triggered", weight=0.9)
        helios.decision(mem, helios.llm_call("llm-1"), "influenced", weight=0.95)
        helios.decision(helios.llm_call("llm-1"), helios.final_answer(run), "caused")
        run.set_output("14 days"); run.set_outcome(correct=False)
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode

from . import semconv as S
from .runtime import get_tracer


@dataclass
class NodeRef:
    """A reference to a node in the causal graph (handle or synthetic)."""

    entity_type: str
    id: str


@dataclass
class _RunState:
    run_id: str
    tenant_id: str
    mcp_seq: int = 0
    step_index: int = 0


_run_ctx: contextvars.ContextVar[_RunState | None] = contextvars.ContextVar(
    "helios_run", default=None
)


def _require_run() -> _RunState:
    state = _run_ctx.get()
    if state is None:
        raise RuntimeError(
            "No active agent_run. Wrap MCP/memory/decision calls in `with helios.agent_run(...)`."
        )
    return state


def _validate(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {label}: {value!r}; allowed: {sorted(allowed)}")
    return value


def _as_ref(obj: object) -> NodeRef:
    if isinstance(obj, NodeRef):
        return obj
    ref = getattr(obj, "ref", None)
    if isinstance(ref, NodeRef):
        return ref
    raise TypeError(f"expected a HELIOS handle or NodeRef, got {type(obj).__name__}")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- Handles ---------------------------------------------------------------
class AgentRunHandle:
    entity_type = "agent_step"

    def __init__(self, span, run_id: str) -> None:
        self._span = span
        self.id = run_id
        self._status: str | None = None

    @property
    def ref(self) -> NodeRef:
        return NodeRef("agent_step", self.id)

    def set_output(self, text: str) -> None:
        self._span.set_attribute(S.AGENT_OUTPUT, text)

    def set_usage(self, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0) -> None:
        self._span.set_attribute(S.USAGE_INPUT_TOKENS, int(input_tokens))
        self._span.set_attribute(S.USAGE_OUTPUT_TOKENS, int(output_tokens))
        self._span.set_attribute(S.USAGE_COST_USD, float(cost_usd))

    def set_outcome(self, correct: bool) -> None:
        self._span.set_attribute(S.OUTCOME_CORRECT, bool(correct))

    def set_status(self, status: str) -> None:
        self._status = _validate(status, S.STATUSES, "status")


class McpHandle:
    entity_type = "mcp_invocation"

    def __init__(self, span, mcp_id: str) -> None:
        self._span = span
        self.id = mcp_id
        self._status = "ok"

    @property
    def ref(self) -> NodeRef:
        return NodeRef("mcp_invocation", self.id)

    def set_result(self, result: str) -> None:
        self._span.set_attribute(S.MCP_TOOL_RESULT, result)

    def fail(self, failure_mode: str, error_type: str = "", message: str = "") -> None:
        _validate(failure_mode, S.FAILURE_MODES, "failure_mode")
        self._status = "error"
        self._span.set_attribute(S.STATUS, "error")
        self._span.set_attribute(S.MCP_FAILURE_MODE, failure_mode)
        if error_type:
            self._span.set_attribute(S.ERROR_TYPE, error_type)
        if message:
            self._span.set_attribute(S.ERROR_MESSAGE, message)
        self._span.set_status(Status(StatusCode.ERROR))


class MemoryHandle:
    entity_type = "memory_operation"

    def __init__(self, span, mem_id: str) -> None:
        self._span = span
        self.id = mem_id

    @property
    def ref(self) -> NodeRef:
        return NodeRef("memory_operation", self.id)

    def set_content(self, content: str) -> None:
        self._span.set_attribute(S.MEMORY_CONTENT, content)


# --- Synthetic node helpers ------------------------------------------------
def llm_call(call_id: str) -> NodeRef:
    """Reference an LLM call node (LLM spans are not a HELIOS table in V1)."""
    return NodeRef("llm_call", call_id)


def final_answer(run: AgentRunHandle) -> NodeRef:
    """Reference the run's final answer node."""
    return NodeRef("final_answer", run.id)


# --- Context managers ------------------------------------------------------
@contextmanager
def agent_run(
    name: str,
    *,
    framework: str = "custom",
    agent_version: str = "",
    agent_id: str = "",
    input: str | None = None,
    tenant_id: str = S.DEFAULT_TENANT,
) -> Iterator[AgentRunHandle]:
    tracer = get_tracer()
    run_id = str(uuid4())
    state = _RunState(run_id=run_id, tenant_id=tenant_id)
    _validate(framework, S.FRAMEWORKS, "framework")

    with tracer.start_as_current_span("helios.agent.run") as span:
        token = _run_ctx.set(state)
        handle = AgentRunHandle(span, run_id)
        span.set_attribute(S.ENTITY, S.ENTITY_AGENT_RUN)
        span.set_attribute(S.SCHEMA_VERSION_KEY, S.SCHEMA_VERSION)
        span.set_attribute(S.TENANT_ID, tenant_id)
        span.set_attribute(S.AGENT_RUN_ID, run_id)
        span.set_attribute(S.AGENT_NAME, name)
        span.set_attribute(S.AGENT_FRAMEWORK, framework)
        if agent_id:
            span.set_attribute(S.AGENT_ID, agent_id)
        if agent_version:
            span.set_attribute(S.AGENT_VERSION, agent_version)
        if input is not None:
            span.set_attribute(S.AGENT_INPUT, input)
        try:
            yield handle
            status = handle._status or "ok"
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(S.AGENT_STATUS, "error")
            _run_ctx.reset(token)
            raise
        span.set_attribute(S.AGENT_STATUS, status)
        _run_ctx.reset(token)


@contextmanager
def mcp_call(
    server_name: str,
    tool_name: str | None = None,
    *,
    protocol_version: str,
    method: str = "tools/call",
    transport: str = "http",
    server_version: str = "",
    arguments: str | None = None,
    permission_scope: str = "",
    permission_changed: bool = False,
) -> Iterator[McpHandle]:
    state = _require_run()
    tracer = get_tracer()
    mcp_id = str(uuid4())
    seq = state.mcp_seq
    state.mcp_seq += 1
    _validate(transport, S.TRANSPORTS, "transport")

    with tracer.start_as_current_span(f"mcp.{method}") as span:
        handle = McpHandle(span, mcp_id)
        span.set_attribute(S.ENTITY, S.ENTITY_MCP)
        span.set_attribute(S.SCHEMA_VERSION_KEY, S.SCHEMA_VERSION)
        span.set_attribute(S.TENANT_ID, state.tenant_id)
        span.set_attribute(S.AGENT_RUN_ID, state.run_id)
        span.set_attribute(S.MCP_INVOCATION_ID, mcp_id)
        span.set_attribute(S.MCP_SERVER_NAME, server_name)
        span.set_attribute(S.MCP_PROTOCOL_VERSION, protocol_version)
        span.set_attribute(S.MCP_METHOD, method)
        span.set_attribute(S.MCP_TRANSPORT, transport)
        span.set_attribute(S.MCP_SEQUENCE_INDEX, seq)
        span.set_attribute(S.MCP_PERMISSION_CHANGED, permission_changed)
        span.set_attribute(S.STATUS, "ok")
        if tool_name:
            span.set_attribute(S.MCP_TOOL_NAME, tool_name)
        if server_version:
            span.set_attribute(S.MCP_SERVER_VERSION, server_version)
        if arguments is not None:
            span.set_attribute(S.MCP_TOOL_ARGUMENTS, arguments)
        if permission_scope:
            span.set_attribute(S.MCP_PERMISSION_SCOPE, permission_scope)
        try:
            yield handle
        except Exception as exc:
            handle.fail("server_error", type(exc).__name__, str(exc))
            raise


@contextmanager
def memory_op(
    op: str,
    *,
    store: str = "",
    namespace: str = "",
    key: str = "",
    query: str | None = None,
    content: str | None = None,
    source: str = "",
    confidence: float | None = None,
    created_at: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> Iterator[MemoryHandle]:
    state = _require_run()
    tracer = get_tracer()
    mem_id = str(uuid4())
    _validate(op, S.MEMORY_OPS, "memory op")

    with tracer.start_as_current_span(f"memory.{op}") as span:
        handle = MemoryHandle(span, mem_id)
        span.set_attribute(S.ENTITY, S.ENTITY_MEMORY)
        span.set_attribute(S.SCHEMA_VERSION_KEY, S.SCHEMA_VERSION)
        span.set_attribute(S.TENANT_ID, state.tenant_id)
        span.set_attribute(S.AGENT_RUN_ID, state.run_id)
        span.set_attribute(S.MEMORY_OPERATION_ID, mem_id)
        span.set_attribute(S.MEMORY_OP, op)
        span.set_attribute(S.STATUS, "ok")
        if store:
            span.set_attribute(S.MEMORY_STORE, store)
        if namespace:
            span.set_attribute(S.MEMORY_NAMESPACE, namespace)
        if key:
            span.set_attribute(S.MEMORY_KEY, key)
        if query is not None:
            span.set_attribute(S.MEMORY_QUERY, query)
        if content is not None:
            span.set_attribute(S.MEMORY_CONTENT, content)
        if source:
            span.set_attribute(S.MEMORY_SOURCE, source)
        if confidence is not None:
            span.set_attribute(S.MEMORY_CONFIDENCE, float(confidence))
        if created_at is not None:
            span.set_attribute(S.MEMORY_CREATED_AT, _iso(created_at))
            age_ms = max(0, int((_now() - created_at).total_seconds() * 1000))
            span.set_attribute(S.MEMORY_AGE_MS, age_ms)
            if stale_after_seconds is not None:
                span.set_attribute(S.MEMORY_IS_STALE, age_ms > stale_after_seconds * 1000)
        yield handle


def decision(
    source: object,
    target: object,
    relation: str,
    *,
    weight: float | None = None,
) -> str:
    """Record a causal edge from ``source`` to ``target``. Returns the edge id."""
    state = _require_run()
    tracer = get_tracer()
    src, dst = _as_ref(source), _as_ref(target)
    _validate(relation, S.DECISION_RELATIONS, "relation")
    _validate(src.entity_type, S.DECISION_SOURCE_TYPES | {"agent_step"}, "source type")
    _validate(dst.entity_type, S.DECISION_TARGET_TYPES, "target type")

    edge_id = str(uuid4())
    step = state.step_index
    state.step_index += 1

    with tracer.start_as_current_span("helios.decision.edge") as span:
        span.set_attribute(S.ENTITY, S.ENTITY_DECISION)
        span.set_attribute(S.SCHEMA_VERSION_KEY, S.SCHEMA_VERSION)
        span.set_attribute(S.TENANT_ID, state.tenant_id)
        span.set_attribute(S.AGENT_RUN_ID, state.run_id)
        span.set_attribute(S.DECISION_EDGE_ID, edge_id)
        span.set_attribute(S.DECISION_SOURCE_TYPE, src.entity_type)
        span.set_attribute(S.DECISION_SOURCE_ID, src.id)
        span.set_attribute(S.DECISION_TARGET_TYPE, dst.entity_type)
        span.set_attribute(S.DECISION_TARGET_ID, dst.id)
        span.set_attribute(S.DECISION_RELATION, relation)
        span.set_attribute(S.DECISION_STEP_INDEX, step)
        if weight is not None:
            span.set_attribute(S.DECISION_WEIGHT, float(weight))
    return edge_id

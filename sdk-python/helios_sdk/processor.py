"""HELIOS span processor — maps finished OTel spans into the four ClickHouse tables.

This is the "transform" layer: instrumentation creates ordinary OpenTelemetry
spans carrying ``helios.*`` / ``mcp.*`` / ``memory.*`` attributes (which also flow
to Tempo via the normal OTLP exporter). This processor additionally reads each
finished span and writes the corresponding structured record so the semantic
HELIOS tables stay populated — without a custom Go collector component.

Spans are routed by the ``helios.entity`` attribute. Child spans (mcp / memory /
decision) end before the enclosing ``agent_run`` span, so the buffer for a run is
flushed atomically when that run span ends.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from . import semconv as S
from .clickhouse import ClickHouseWriter


def _hex_trace(span: ReadableSpan) -> str:
    return format(span.get_span_context().trace_id, "032x")


def _hex_span(span: ReadableSpan) -> str:
    return format(span.get_span_context().span_id, "016x")


def _hex_parent(span: ReadableSpan) -> str:
    parent = span.parent
    return format(parent.span_id, "016x") if parent else ""


def _iso(ns: int | None) -> str | None:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _duration_ms(span: ReadableSpan) -> int:
    if span.start_time is None or span.end_time is None:
        return 0
    return max(0, round((span.end_time - span.start_time) / 1e6))


def _b(value: Any) -> int | None:
    """Bool -> ClickHouse UInt8 (0/1); None stays None."""
    if value is None:
        return None
    return 1 if value else 0


class HeliosSpanProcessor(SpanProcessor):
    def __init__(self, writer: ClickHouseWriter | None = None) -> None:
        self._writer = writer or ClickHouseWriter()
        # run_id -> { table_name: [rows] }
        self._buffers: dict[str, dict[str, list[dict]]] = {}

    # -- SpanProcessor interface -------------------------------------------
    def on_start(self, span, parent_context=None) -> None:  # noqa: D401
        return None

    def on_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        entity = attrs.get(S.ENTITY)
        if entity is None:
            return  # not a HELIOS span (e.g. a plain LLM span)

        run_id = attrs.get(S.AGENT_RUN_ID, "_orphan")
        run_buf = self._buffers.setdefault(run_id, {})

        if entity == S.ENTITY_MCP:
            run_buf.setdefault("mcp_invocation", []).append(self._mcp_row(span, attrs))
        elif entity == S.ENTITY_MEMORY:
            run_buf.setdefault("memory_operation", []).append(self._memory_row(span, attrs))
        elif entity == S.ENTITY_DECISION:
            run_buf.setdefault("decision_edge", []).append(self._decision_row(span, attrs))
        elif entity == S.ENTITY_AGENT_RUN:
            run_buf.setdefault("agent_run", []).append(self._agent_run_row(span, attrs))
            # The run span is outermost: flush everything for this run now.
            self._flush_run(run_id)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        for run_id in list(self._buffers):
            self._flush_run(run_id)
        return True

    def shutdown(self) -> None:
        self.force_flush()

    # -- internals ----------------------------------------------------------
    def _flush_run(self, run_id: str) -> None:
        buf = self._buffers.pop(run_id, None)
        if not buf:
            return
        # Insert children first, run last (matches FK-style reading order).
        for table in ("mcp_invocation", "memory_operation", "decision_edge", "agent_run"):
            self._writer.insert(table, buf.get(table, []))

    def _envelope(self, attrs: dict) -> dict:
        return {
            "schema_version": attrs.get(S.SCHEMA_VERSION_KEY, S.SCHEMA_VERSION),
            "tenant_id": attrs.get(S.TENANT_ID, S.DEFAULT_TENANT),
            "agent_run_id": attrs.get(S.AGENT_RUN_ID),
        }

    def _agent_run_row(self, span: ReadableSpan, attrs: dict) -> dict:
        row = self._envelope(attrs)
        row.update(
            {
                "trace_id": _hex_trace(span),
                "root_span_id": _hex_span(span),
                "agent_name": attrs.get(S.AGENT_NAME, ""),
                "agent_id": attrs.get(S.AGENT_ID, ""),
                "agent_version": attrs.get(S.AGENT_VERSION, ""),
                "agent_framework": attrs.get(S.AGENT_FRAMEWORK, "custom"),
                "input": attrs.get(S.AGENT_INPUT, ""),
                "output": attrs.get(S.AGENT_OUTPUT, ""),
                "status": attrs.get(S.AGENT_STATUS, "ok"),
                "start_time": _iso(span.start_time),
                "end_time": _iso(span.end_time),
                "duration_ms": _duration_ms(span),
                "input_tokens": attrs.get(S.USAGE_INPUT_TOKENS, 0),
                "output_tokens": attrs.get(S.USAGE_OUTPUT_TOKENS, 0),
                "cost_usd": attrs.get(S.USAGE_COST_USD, 0),
            }
        )
        correct = _b(attrs.get(S.OUTCOME_CORRECT))
        if correct is not None:
            row["outcome_correct"] = correct
        return row

    def _mcp_row(self, span: ReadableSpan, attrs: dict) -> dict:
        row = self._envelope(attrs)
        row.update(
            {
                "mcp_invocation_id": attrs.get(S.MCP_INVOCATION_ID),
                "trace_id": _hex_trace(span),
                "span_id": _hex_span(span),
                "parent_span_id": _hex_parent(span),
                "server_name": attrs.get(S.MCP_SERVER_NAME, ""),
                "server_version": attrs.get(S.MCP_SERVER_VERSION, ""),
                "transport": attrs.get(S.MCP_TRANSPORT, ""),
                "protocol_version": attrs.get(S.MCP_PROTOCOL_VERSION, ""),
                "method": attrs.get(S.MCP_METHOD, ""),
                "tool_name": attrs.get(S.MCP_TOOL_NAME, ""),
                "tool_arguments": attrs.get(S.MCP_TOOL_ARGUMENTS, ""),
                "tool_result": attrs.get(S.MCP_TOOL_RESULT, ""),
                "permission_scope": attrs.get(S.MCP_PERMISSION_SCOPE, ""),
                "permission_changed": _b(attrs.get(S.MCP_PERMISSION_CHANGED, False)),
                "sequence_index": attrs.get(S.MCP_SEQUENCE_INDEX, 0),
                "latency_ms": _duration_ms(span),
                "status": attrs.get(S.STATUS, "ok"),
                "error_type": attrs.get(S.ERROR_TYPE, ""),
                "error_message": attrs.get(S.ERROR_MESSAGE, ""),
                "failure_mode": attrs.get(S.MCP_FAILURE_MODE, ""),
                "event_time": _iso(span.start_time),
            }
        )
        return row

    def _memory_row(self, span: ReadableSpan, attrs: dict) -> dict:
        row = self._envelope(attrs)
        row.update(
            {
                "memory_operation_id": attrs.get(S.MEMORY_OPERATION_ID),
                "trace_id": _hex_trace(span),
                "span_id": _hex_span(span),
                "op": attrs.get(S.MEMORY_OP, ""),
                "store": attrs.get(S.MEMORY_STORE, ""),
                "namespace": attrs.get(S.MEMORY_NAMESPACE, ""),
                "key": attrs.get(S.MEMORY_KEY, ""),
                "query": attrs.get(S.MEMORY_QUERY, ""),
                "content": attrs.get(S.MEMORY_CONTENT, ""),
                "source": attrs.get(S.MEMORY_SOURCE, ""),
                "latency_ms": _duration_ms(span),
                "status": attrs.get(S.STATUS, "ok"),
                "event_time": _iso(span.start_time),
            }
        )
        for key, col in (
            (S.MEMORY_CONFIDENCE, "confidence"),
            (S.MEMORY_CREATED_AT, "created_at"),
            (S.MEMORY_AGE_MS, "age_ms"),
        ):
            if attrs.get(key) is not None:
                row[col] = attrs[key]
        stale = _b(attrs.get(S.MEMORY_IS_STALE))
        if stale is not None:
            row["is_stale"] = stale
        return row

    def _decision_row(self, span: ReadableSpan, attrs: dict) -> dict:
        row = self._envelope(attrs)
        row.update(
            {
                "decision_edge_id": attrs.get(S.DECISION_EDGE_ID),
                "trace_id": _hex_trace(span),
                "source_type": attrs.get(S.DECISION_SOURCE_TYPE, ""),
                "source_id": attrs.get(S.DECISION_SOURCE_ID, ""),
                "target_type": attrs.get(S.DECISION_TARGET_TYPE, ""),
                "target_id": attrs.get(S.DECISION_TARGET_ID, ""),
                "relation": attrs.get(S.DECISION_RELATION, ""),
                "step_index": attrs.get(S.DECISION_STEP_INDEX, 0),
                "event_time": _iso(span.start_time),
            }
        )
        if attrs.get(S.DECISION_WEIGHT) is not None:
            row["weight"] = attrs[S.DECISION_WEIGHT]
        return row

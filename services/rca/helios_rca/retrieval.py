"""Telemetry retrieval — assemble a structured context for one agent run.

Pulls the agent_run, its MCP invocations, memory operations, and decision edges
from ClickHouse into a single ``RunContext``. This is the grounding the reasoner
works from — the reasoner is never asked to invent facts, only to explain these.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clickhouse import ClickHouse


@dataclass
class RunContext:
    run: dict
    mcp: list[dict] = field(default_factory=list)
    memory: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.run["agent_run_id"]

    @property
    def trace_id(self) -> str:
        return self.run.get("trace_id", "")


def _esc(value: str) -> str:
    return value.replace("'", "''")


def load_run_context(ch: ClickHouse, run_id: str) -> RunContext | None:
    rid = _esc(run_id)
    runs = ch.query_json(
        f"SELECT *, toString(agent_run_id) AS agent_run_id "
        f"FROM helios.agent_run WHERE agent_run_id = '{rid}' LIMIT 1"
    )
    if not runs:
        return None

    mcp = ch.query_json(
        f"SELECT *, toString(agent_run_id) AS agent_run_id, "
        f"toString(mcp_invocation_id) AS mcp_invocation_id "
        f"FROM helios.mcp_invocation WHERE agent_run_id = '{rid}' ORDER BY sequence_index"
    )
    memory = ch.query_json(
        f"SELECT *, toString(agent_run_id) AS agent_run_id, "
        f"toString(memory_operation_id) AS memory_operation_id "
        f"FROM helios.memory_operation WHERE agent_run_id = '{rid}' ORDER BY age_ms DESC"
    )
    edges = ch.query_json(
        f"SELECT *, toString(agent_run_id) AS agent_run_id, "
        f"toString(decision_edge_id) AS decision_edge_id "
        f"FROM helios.decision_edge WHERE agent_run_id = '{rid}' ORDER BY step_index"
    )
    return RunContext(run=runs[0], mcp=mcp, memory=memory, edges=edges)

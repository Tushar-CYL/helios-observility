"""Detectors — turn raw telemetry into grounded candidate causes.

Each detector inspects the :class:`RunContext` and, if its signal is present,
emits a :class:`Candidate` with a base weight and concrete evidence (real entity
ids, latencies, ages). The reasoner ranks and narrates these candidates; it never
invents new ones. This is what keeps HELIOS RCA grounded rather than hallucinated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .retrieval import RunContext


@dataclass
class Candidate:
    code: str
    hypothesis: str
    weight: float  # unnormalized signal strength (0..1)
    evidence: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


Detector = Callable[[RunContext], "Candidate | None"]
_REGISTRY: list[Detector] = []


def _num(value: object, default: float = 0.0) -> float:
    """Coerce a telemetry value to float (ClickHouse serializes UInt64 as str)."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def detector(fn: Detector) -> Detector:
    _REGISTRY.append(fn)
    return fn


@detector
def mcp_timeout(ctx: RunContext) -> Candidate | None:
    hits = [m for m in ctx.mcp if m.get("status") == "error" and m.get("failure_mode") == "timeout"]
    if not hits:
        return None
    m = hits[0]
    latency = int(_num(m.get("latency_ms")))
    return Candidate(
        code="mcp_timeout",
        hypothesis=(
            f"The MCP call to '{m['server_name']}' "
            f"({m.get('tool_name') or m.get('method')}) timed out after "
            f"{latency} ms, so the agent could not reach the "
            f"authoritative source."
        ),
        weight=0.85,
        evidence=[
            f"{m['server_name']} {m.get('method')} status=error failure_mode=timeout "
            f"latency={latency}ms"
        ],
        evidence_refs=[m["mcp_invocation_id"]],
    )


@detector
def stale_memory(ctx: RunContext) -> Candidate | None:
    stale = [m for m in ctx.memory if m.get("is_stale") in (1, True)]
    if not stale:
        return None
    m = stale[0]
    age_days = round(_num(m.get("age_ms")) / 86_400_000, 1)
    return Candidate(
        code="stale_memory",
        hypothesis=(
            f"The agent read a stale memory '{m.get('key')}' from "
            f"'{m.get('store')}' that was {age_days} days old, likely supplying an "
            f"outdated value to the model."
        ),
        weight=0.9,
        evidence=[
            f"memory '{m.get('key')}' age={age_days}d is_stale=1 "
            f"source={m.get('source')} confidence={m.get('confidence')}"
        ],
        evidence_refs=[m["memory_operation_id"]],
    )


@detector
def stale_after_timeout(ctx: RunContext) -> Candidate | None:
    """The dangerous combination: a timeout forced a fallback to stale memory."""
    has_timeout = any(
        m.get("status") == "error" and m.get("failure_mode") == "timeout" for m in ctx.mcp
    )
    triggered = [
        e
        for e in ctx.edges
        if e.get("source_type") == "mcp_invocation"
        and e.get("target_type") == "memory_operation"
        and e.get("relation") == "triggered"
    ]
    if not (has_timeout and triggered):
        return None
    return Candidate(
        code="stale_after_timeout",
        hypothesis=(
            "An MCP timeout triggered a silent fallback to stale cached memory, "
            "which then influenced the model's answer — a fail-soft path that hid "
            "the real failure from the user."
        ),
        weight=0.95,
        evidence=["decision edge: mcp_invocation --triggered--> memory_operation"],
        evidence_refs=[e["decision_edge_id"] for e in triggered],
    )


@detector
def permission_change(ctx: RunContext) -> Candidate | None:
    hits = [m for m in ctx.mcp if m.get("permission_changed") in (1, True)]
    if not hits:
        return None
    m = hits[0]
    return Candidate(
        code="permission_change",
        hypothesis=(
            f"The permission scope changed on the call to '{m['server_name']}' "
            f"(scope='{m.get('permission_scope')}'), which may have altered access "
            f"or behavior."
        ),
        weight=0.4,
        evidence=[f"{m['server_name']} permission_changed=1 scope={m.get('permission_scope')}"],
        evidence_refs=[m["mcp_invocation_id"]],
    )


@detector
def no_eval_gate(ctx: RunContext) -> Candidate | None:
    if ctx.run.get("outcome_correct") == 0:
        return Candidate(
            code="no_eval_gate",
            hypothesis=(
                "The wrong answer reached the user with no evaluation/quality gate "
                "to catch it before responding."
            ),
            weight=0.3,
            evidence=["agent_run.outcome_correct=0 and no eval blocked the response"],
            evidence_refs=[],
        )
    return None


def detect_candidates(ctx: RunContext) -> list[Candidate]:
    found = [c for d in _REGISTRY if (c := d(ctx)) is not None]
    found.sort(key=lambda c: c.weight, reverse=True)
    return found


def normalize(candidates: list[Candidate]) -> list[tuple[Candidate, float]]:
    """Return (candidate, probability) with probabilities summing to ~1."""
    total = sum(c.weight for c in candidates)
    if total <= 0:
        return [(c, 0.0) for c in candidates]
    return [(c, round(c.weight / total, 3)) for c in candidates]

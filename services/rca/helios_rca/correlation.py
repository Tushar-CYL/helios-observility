"""Business Outcome Correlation — link KPI shifts to agent-runtime telemetry.

This is the V3 moat: turning engineering signals into executive answers. Given a
business metric (e.g. support-resolution rate) that moved, it finds the technical
driver in the same time window and produces a quantified, human-readable link:

    "Support resolution dropped 14.0% in this window. Most correlated driver:
     MCP error rate rose to 33% (baseline 4%). Likely cause of the decline."

Grounded, like the rest of HELIOS: drivers are computed from real telemetry; the
optional LLM only narrates them.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .clickhouse import ClickHouse


def _iso_now() -> str:
    # Small buffer so the latest bucket's events are inside the window.
    return (datetime.now(tz=timezone.utc) + timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


@dataclass
class Factor:
    factor: str
    weight: float
    evidence: str


@dataclass
class CorrelationResult:
    metric: str
    segment: str
    window_start: str
    window_end: str
    baseline_value: float
    current_value: float
    delta_pct: float
    direction: str
    driver_metric: str
    driver_value: float
    driver_baseline: float
    correlation_score: float
    summary: str
    factors: list[Factor] = field(default_factory=list)
    reasoner: str = "heuristic"
    model: str = ""

    def to_row(self, tenant_id: str) -> dict:
        return {
            "schema_version": "1.0.0",
            "tenant_id": tenant_id,
            "metric": self.metric,
            "segment": self.segment,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "delta_pct": round(self.delta_pct, 2),
            "direction": self.direction,
            "driver_metric": self.driver_metric,
            "driver_value": round(self.driver_value, 4),
            "driver_baseline": round(self.driver_baseline, 4),
            "correlation_score": round(self.correlation_score, 3),
            "summary": self.summary,
            "factors": json.dumps([asdict(f) for f in self.factors]),
            "reasoner": self.reasoner,
            "model": self.model,
        }


# Driver telemetry metrics, each a SQL expression over agent_run joined context.
# Computed for current window vs baseline window; higher = worse.
_DRIVERS = {
    "wrong_answer_rate": "Share of runs whose final answer was incorrect",
    "mcp_error_rate": "Share of MCP tool calls that failed",
    "stale_memory_rate": "Share of memory reads that were stale",
    "agent_error_rate": "Share of agent runs that errored",
}


def _esc(v: str) -> str:
    return v.replace("'", "''")


def _scalar(ch: ClickHouse, sql: str, default: float = 0.0) -> float:
    rows = ch.query_json(sql)
    if not rows:
        return default
    val = next(iter(rows[0].values()))
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _driver_rate(ch: ClickHouse, tenant: str, metric: str, start: str, end: str) -> float:
    t, s, e = _esc(tenant), _esc(start), _esc(end)
    ts0 = f"parseDateTimeBestEffort('{s}')"
    ts1 = f"parseDateTimeBestEffort('{e}')"
    win = f"tenant_id = '{t}' AND start_time >= {ts0} AND start_time < {ts1}"
    if metric == "wrong_answer_rate":
        return _scalar(
            ch,
            f"SELECT countIf(outcome_correct = 0) / nullIf(count(), 0) AS v "
            f"FROM helios.agent_run WHERE {win}",
        )
    if metric == "agent_error_rate":
        return _scalar(
            ch,
            f"SELECT countIf(status = 'error') / nullIf(count(), 0) AS v "
            f"FROM helios.agent_run WHERE {win}",
        )
    if metric == "mcp_error_rate":
        mwin = f"tenant_id = '{t}' AND event_time >= {ts0} AND event_time < {ts1}"
        return _scalar(
            ch,
            f"SELECT countIf(status = 'error') / nullIf(count(), 0) AS v "
            f"FROM helios.mcp_invocation WHERE {mwin}",
        )
    if metric == "stale_memory_rate":
        mwin = f"tenant_id = '{t}' AND event_time >= {ts0} AND event_time < {ts1}"
        return _scalar(
            ch,
            f"SELECT countIf(is_stale = 1) / nullIf(countIf(op = 'read'), 0) AS v "
            f"FROM helios.memory_operation WHERE {mwin}",
        )
    return 0.0


def correlate(
    ch: ClickHouse,
    tenant_id: str,
    metric: str,
    segment: str = "all",
    baseline_points: int = 3,
) -> CorrelationResult | None:
    """Compare the latest KPI bucket to a baseline and find the top telemetry driver."""
    t, m, seg = _esc(tenant_id), _esc(metric), _esc(segment)
    series = ch.query_json(
        f"SELECT toString(bucket_start) AS bucket_start, value "
        f"FROM helios.business_kpi "
        f"WHERE tenant_id = '{t}' AND metric = '{m}' AND segment = '{seg}' "
        f"ORDER BY bucket_start DESC LIMIT {baseline_points + 1}"
    )
    if len(series) < 2:
        return None

    current = series[0]
    baseline_rows = series[1:]
    current_val = float(current["value"])
    baseline_val = sum(float(r["value"]) for r in baseline_rows) / len(baseline_rows)
    if baseline_val == 0:
        return None
    delta_pct = (current_val - baseline_val) / abs(baseline_val) * 100.0
    direction = "drop" if delta_pct < -1 else "rise" if delta_pct > 1 else "flat"

    # A KPI bucket reflects what happened *during* that bucket, so the driver
    # window for the latest bucket runs from its start to now; the baseline
    # driver window spans the earlier buckets.
    current_bucket = current["bucket_start"]
    oldest_bucket = baseline_rows[-1]["bucket_start"]
    window_start = current_bucket
    window_end = _iso_now()

    base_window_start = oldest_bucket
    base_window_end = current_bucket

    # Score each driver by how much it rose in the current window vs baseline.
    factors: list[Factor] = []
    best = None
    for dm in _DRIVERS:
        cur = _driver_rate(ch, tenant_id, dm, window_start, window_end)
        base = _driver_rate(ch, tenant_id, dm, base_window_start, base_window_end)
        rise = cur - base
        if cur <= 0 and base <= 0:
            continue
        # Correlation strength: driver got worse AND KPI moved adversely.
        adverse = (direction == "drop" and rise > 0) or (direction == "rise" and rise > 0)
        score = max(0.0, min(1.0, rise)) if adverse else max(0.0, min(1.0, rise)) * 0.3
        factors.append(
            Factor(
                factor=dm,
                weight=round(score, 3),
                evidence=f"{dm} = {cur:.0%} (baseline {base:.0%})",
            )
        )
        if best is None or score > best[1]:
            best = (dm, score, cur, base)

    factors.sort(key=lambda f: f.weight, reverse=True)
    if best is None:
        driver_metric, correlation_score, driver_value, driver_baseline = ("", 0.0, 0.0, 0.0)
    else:
        driver_metric, correlation_score, driver_value, driver_baseline = best

    summary = _summarize(metric, delta_pct, direction, driver_metric, driver_value, driver_baseline)
    return CorrelationResult(
        metric=metric,
        segment=segment,
        window_start=window_start,
        window_end=window_end,
        baseline_value=round(baseline_val, 4),
        current_value=round(current_val, 4),
        delta_pct=delta_pct,
        direction=direction,
        driver_metric=driver_metric,
        driver_value=driver_value,
        driver_baseline=driver_baseline,
        correlation_score=correlation_score,
        summary=summary,
        factors=factors,
    )


_PRETTY = {
    "wrong_answer_rate": "wrong-answer rate",
    "mcp_error_rate": "MCP tool error rate",
    "stale_memory_rate": "stale-memory rate",
    "agent_error_rate": "agent error rate",
}


def _summarize(
    metric: str, delta_pct: float, direction: str, driver: str, dv: float, db: float
) -> str:
    metric_h = metric.replace("_", " ")
    if direction == "flat" or not driver:
        return f"{metric_h} was stable in this window; no strong technical driver found."
    move = f"{'dropped' if direction == 'drop' else 'rose'} {abs(delta_pct):.1f}%"
    return (
        f"{metric_h.capitalize()} {move} in this window. "
        f"Most correlated driver: {_PRETTY.get(driver, driver)} "
        f"{dv:.0%} (baseline {db:.0%})."
    )

#!/usr/bin/env python3
"""HELIOS V3 — seed business KPIs + matching telemetry for the correlation demo.

Tells the executive-facing story: support-resolution rate was healthy, then
dropped sharply in the latest window — at the same time agent runs started
failing (wrong answers from stale memory + MCP timeouts). The correlation engine
links the two.

Seeds, for the last N hourly buckets:
  * business_kpi: support_resolution_rate (high, then a drop) + revenue_per_hour
  * agent_run / mcp_invocation / memory_operation: healthy in baseline buckets,
    failing in the latest bucket.

Container-free (stdlib only). Run after the stack is up:
    python deploy/scripts/seed_outcomes.py
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

BASE = "http://localhost:8123"
USER, PWD = "helios", "helios"
TENANT = "default"
BUCKETS = 6  # hourly buckets, oldest..newest


def ch(sql: str, body: bytes | None = None) -> str:
    params = {"user": USER, "password": PWD, "database": "helios",
              "date_time_input_format": "best_effort", "query": sql}
    req = urllib.request.Request(f"{BASE}/?{urllib.parse.urlencode(params)}", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def insert(table: str, rows: list[dict]) -> None:
    body = "\n".join(json.dumps(r, default=str) for r in rows).encode()
    ch(f"INSERT INTO helios.{table} FORMAT JSONEachRow", body)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main() -> int:
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = [now - timedelta(hours=BUCKETS - 1 - i) for i in range(BUCKETS)]

    # Clear prior demo data for a clean story.
    for tbl in ("business_kpi", "outcome_correlation"):
        ch(f"TRUNCATE TABLE IF EXISTS helios.{tbl}")

    # --- KPIs: healthy, then a drop in the last bucket ---------------------
    kpi_rows = []
    for i, b in enumerate(buckets):
        last = i == len(buckets) - 1
        resolution = 92.0 if not last else 78.0      # % resolved -> drops 14%
        revenue = 1200.0 if not last else 1020.0      # $/hour -> drops too
        for metric, value, unit in (
            ("support_resolution_rate", resolution, "percent"),
            ("revenue_per_hour", revenue, "usd"),
        ):
            kpi_rows.append({
                "schema_version": "1.0.0", "tenant_id": TENANT, "kpi_id": str(uuid.uuid4()),
                "metric": metric, "unit": unit, "bucket_start": iso(b),
                "value": value, "segment": "all",
            })
    insert("business_kpi", kpi_rows)

    # --- Telemetry: healthy baseline, failing in the last bucket ----------
    runs, mcps, mems = [], [], []
    for i, b in enumerate(buckets):
        last = i == len(buckets) - 1
        n_runs = 10
        n_bad = 6 if last else 0   # 60% wrong answers in the bad window
        for k in range(n_runs):
            rid = str(uuid.uuid4())
            bad = k < n_bad
            trace = uuid.uuid4().hex
            ts = b + timedelta(minutes=k)
            runs.append({
                "schema_version": "1.0.0", "tenant_id": TENANT, "agent_run_id": rid,
                "trace_id": trace, "root_span_id": uuid.uuid4().hex[:16],
                "agent_name": "support-assistant", "agent_framework": "langgraph",
                "input": "refund window?", "output": "14 days" if bad else "30 days",
                "status": "ok", "start_time": iso(ts), "end_time": iso(ts + timedelta(seconds=2)),
                "duration_ms": 2000, "input_tokens": 400, "output_tokens": 30,
                "cost_usd": 0.002, "outcome_correct": 0 if bad else 1,
            })
            # one MCP call per run; in the bad window it times out
            mcps.append({
                "schema_version": "1.0.0", "tenant_id": TENANT, "agent_run_id": rid,
                "mcp_invocation_id": str(uuid.uuid4()), "trace_id": trace,
                "span_id": uuid.uuid4().hex[:16], "server_name": "policy-db-mcp",
                "protocol_version": "2025-06-18", "method": "tools/call", "tool_name": "fetch_policy",
                "transport": "http", "sequence_index": 0,
                "latency_ms": 5000 if bad else 120,
                "status": "error" if bad else "ok",
                "failure_mode": "timeout" if bad else "",
                "error_type": "DeadlineExceeded" if bad else "",
                "event_time": iso(ts),
            })
            # memory read; stale in the bad window
            mems.append({
                "schema_version": "1.0.0", "tenant_id": TENANT, "agent_run_id": rid,
                "memory_operation_id": str(uuid.uuid4()), "trace_id": trace,
                "span_id": uuid.uuid4().hex[:16], "op": "read", "store": "redis",
                "namespace": "policy-cache", "key": "refund-window",
                "is_stale": 1 if bad else 0,
                "age_ms": 13_400_000_000 if bad else 60_000,
                "status": "ok", "event_time": iso(ts),
            })
    insert("agent_run", runs)
    insert("mcp_invocation", mcps)
    insert("memory_operation", mems)

    print(f"Seeded {len(kpi_rows)} KPI points + {len(runs)} runs across {BUCKETS} hourly buckets.")
    print("Latest window shows support_resolution_rate dropping 14% with failing telemetry.")

    # Run the correlation so the Business Outcomes dashboard is populated end-to-end.
    # Best-effort: if the RCA service isn't up yet, print the manual command.
    _correlate("support_resolution_rate")
    _correlate("revenue_per_hour")
    return 0


def _correlate(metric: str, rca_url: str = "http://localhost:8088") -> None:
    url = f"{rca_url}/correlate?metric={metric}"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
        print(f"  correlated {metric}: {body.get('summary')}")
    except Exception:  # noqa: BLE001 — best-effort convenience step
        print(f"  (RCA service not reachable; run later: "
              f"curl -X POST '{url}')")


if __name__ == "__main__":
    raise SystemExit(main())

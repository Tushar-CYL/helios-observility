#!/usr/bin/env python3
"""HELIOS V1 — Phase 2 OTLP smoke test.

Sends one trace, one log, and one metric to the collector's OTLP/HTTP endpoint,
then you can verify they landed in Tempo / Loki / Mimir / ClickHouse.

Container-free and dependency-free: uses only the Python standard library.

Usage:
    python deploy/scripts/smoke_test.py            # defaults to localhost:4318
    python deploy/scripts/smoke_test.py http://localhost:4318
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4318").rstrip("/")

# A fixed, recognizable trace id so it is easy to find later in Tempo/ClickHouse.
TRACE_ID = "00000000000000000000000000515105"  # "HELIOS-ish"
SPAN_ID = "0000000000515105"
NOW_NS = time.time_ns()
START_NS = NOW_NS - 5_000_000  # 5ms span


def post(path: str, payload: dict) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
        print(f"  {path} -> {resp.status} {body or '{}'}")
        return resp.status


RESOURCE = {
    "attributes": [
        {"key": "service.name", "value": {"stringValue": "helios-smoke-test"}},
        {"key": "helios.schema_version", "value": {"stringValue": "1.0.0"}},
        {"key": "helios.tenant_id", "value": {"stringValue": "default"}},
    ]
}


def traces_payload() -> dict:
    return {
        "resourceSpans": [
            {
                "resource": RESOURCE,
                "scopeSpans": [
                    {
                        "scope": {"name": "helios.smoke"},
                        "spans": [
                            {
                                "traceId": TRACE_ID,
                                "spanId": SPAN_ID,
                                "name": "helios.smoke.span",
                                "kind": 1,
                                "startTimeUnixNano": str(START_NS),
                                "endTimeUnixNano": str(NOW_NS),
                                "attributes": [
                                    {"key": "helios.check", "value": {"stringValue": "trace"}}
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def logs_payload() -> dict:
    return {
        "resourceLogs": [
            {
                "resource": RESOURCE,
                "scopeLogs": [
                    {
                        "scope": {"name": "helios.smoke"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(NOW_NS),
                                "severityNumber": 9,
                                "severityText": "INFO",
                                "body": {"stringValue": "helios smoke-test log line"},
                                "traceId": TRACE_ID,
                                "spanId": SPAN_ID,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def metrics_payload() -> dict:
    return {
        "resourceMetrics": [
            {
                "resource": RESOURCE,
                "scopeMetrics": [
                    {
                        "scope": {"name": "helios.smoke"},
                        "metrics": [
                            {
                                "name": "helios_smoke_check",
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "asInt": "1",
                                            "timeUnixNano": str(NOW_NS),
                                            "attributes": [
                                                {"key": "check", "value": {"stringValue": "metric"}}
                                            ],
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def main() -> int:
    print(f"HELIOS smoke test -> {BASE}")
    print(f"  trace_id = {TRACE_ID}")
    failures = 0
    for path, payload in (
        ("/v1/traces", traces_payload()),
        ("/v1/logs", logs_payload()),
        ("/v1/metrics", metrics_payload()),
    ):
        try:
            status = post(path, payload)
            if status >= 300:
                failures += 1
        except urllib.error.URLError as exc:
            print(f"  {path} -> ERROR {exc}")
            failures += 1

    print("\nSent. Verify with:")
    print('  docker exec helios-clickhouse clickhouse-client --user helios --password helios \\')
    print('    --query "SELECT count() FROM helios.otel_traces"')
    print(f'  curl "http://localhost:3200/api/traces/{TRACE_ID}"')
    print('  # Mimir renames unit=1 gauges with a _ratio suffix:')
    print('  curl "http://localhost:9009/prometheus/api/v1/query?query=helios_smoke_check_ratio"')
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

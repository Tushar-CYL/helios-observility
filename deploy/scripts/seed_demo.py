#!/usr/bin/env python3
"""HELIOS V1 — seed the golden demo fixture into ClickHouse.

Loads the Phase 1 example records (the "stale-memory multi-MCP hallucination"
scenario) into the four HELIOS tables via the ClickHouse HTTP interface, so the
Grafana dashboards have a real, causally-complete agent run to display before
the Phase 3 SDK exists.

Container-free and dependency-free (Python standard library only). It reuses the
exact JSON fixtures under schema/examples/ — the same files the Phase 1
validation harness checks — so seeded data can never drift from the schema.

Usage:
    python deploy/scripts/seed_demo.py
    python deploy/scripts/seed_demo.py --base http://localhost:8123 --reset
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "schema" / "examples"

# table -> (example file, is the example a JSON array?)
TABLES = {
    "agent_run": ("agent_run.json", False),
    "mcp_invocation": ("mcp_invocation.json", True),
    "memory_operation": ("memory_operation.json", True),
    "decision_edge": ("decision_edge.json", True),
}

# JSON booleans must become 0/1 for ClickHouse UInt8 columns.
BOOL_FIELDS = {"outcome_correct", "permission_changed", "is_stale"}


def normalize(record: dict) -> dict:
    out = dict(record)
    for field in BOOL_FIELDS:
        if isinstance(out.get(field), bool):
            out[field] = 1 if out[field] else 0
    return out


def load_rows(example_file: str, is_list: bool) -> list[dict]:
    raw = json.loads((EXAMPLES / example_file).read_text(encoding="utf-8"))
    records = raw if is_list else [raw]
    return [normalize(r) for r in records]


def ch_query(base: str, user: str, password: str, sql: str, body: bytes | None = None) -> str:
    params = {
        "user": user,
        "password": password,
        "query": sql,
        # Accept ISO-8601 timestamps ("2026-06-12T09:30:00.000000000Z") in JSON.
        "date_time_input_format": "best_effort",
    }
    url = f"{base}/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"ClickHouse error during [{sql}]:\n  {detail}") from None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--user", default="helios")
    ap.add_argument("--password", default="helios")
    ap.add_argument("--reset", action="store_true", help="truncate tables before seeding")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"HELIOS demo seed -> {base} (db=helios)")
    total = 0
    for table, (example_file, is_list) in TABLES.items():
        rows = load_rows(example_file, is_list)
        if args.reset:
            ch_query(base, args.user, args.password, f"TRUNCATE TABLE IF EXISTS helios.{table}")
        payload = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
        sql = f"INSERT INTO helios.{table} FORMAT JSONEachRow"
        ch_query(base, args.user, args.password, sql, body=payload)
        count = ch_query(
            base, args.user, args.password,
            f"SELECT count() FROM helios.{table}",
        )
        print(f"  {table:<18} inserted {len(rows):>2}  (table total: {count})")
        total += len(rows)

    print(f"\nSeeded {total} records for the golden demo run.")
    print("Open Grafana at http://localhost:3000 -> HELIOS dashboards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

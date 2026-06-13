#!/usr/bin/env python3
"""HELIOS V1 schema validation harness.

Phase 1 verification tool. Container-free: requires only Python + jsonschema.
It proves the data model is internally consistent *before* any of it is poured
into the ClickHouse container in Phase 2.

It performs two layers of checking:

1. **Structural** — every example record validates against its JSON Schema
   (draft 2020-12), including cross-file ``$ref`` resolution.
2. **Referential** — the records form a coherent agent run: shared
   ``agent_run_id``, consistent ``schema_version``, MCP errors carry a
   ``failure_mode``, and every ``decision_edge`` endpoint resolves to a real
   record (or an allowed synthetic node such as ``llm_call``/``final_answer``).
   It also asserts the golden causal path exists:
   ``stale memory_operation -> llm_call -> final_answer``.

Exit code 0 = all checks passed; 1 = at least one failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "jsonschema"
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
EXPECTED_SCHEMA_VERSION = "1.0.0"

# entity -> (schema file, example file, is the example a list?)
ENTITIES = {
    "agent_run": ("agent_run.schema.json", "agent_run.json", False),
    "mcp_invocation": ("mcp_invocation.schema.json", "mcp_invocation.json", True),
    "memory_operation": ("memory_operation.schema.json", "memory_operation.json", True),
    "decision_edge": ("decision_edge.schema.json", "decision_edge.json", True),
}

# decision_edge endpoint types that are not stored as their own records in V1.
SYNTHETIC_NODE_TYPES = {"llm_call", "final_answer", "agent_step"}


def _load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _build_registry() -> Registry:
    """Register every local schema by its ``$id`` so ``$ref`` resolves offline."""
    registry = Registry()
    for schema_path in SCHEMA_DIR.glob("*.schema.json"):
        contents = _load_json(schema_path)
        registry = registry.with_resource(
            uri=contents["$id"],
            resource=Resource.from_contents(contents),
        )
    return registry


class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)
        print(f"  PASS  {msg}")

    def fail(self, msg: str) -> None:
        self.failed.append(msg)
        print(f"  FAIL  {msg}")

    def section(self, title: str) -> None:
        print(f"\n{title}")


def as_records(raw, is_list: bool) -> list[dict]:
    return list(raw) if is_list else [raw]


def structural_checks(report: Report, registry: Registry) -> dict[str, list[dict]]:
    report.section("Structural validation (JSON Schema)")
    loaded: dict[str, list[dict]] = {}
    for entity, (schema_file, example_file, is_list) in ENTITIES.items():
        schema = _load_json(SCHEMA_DIR / schema_file)
        validator = Draft202012Validator(schema, registry=registry)
        records = as_records(_load_json(EXAMPLES_DIR / example_file), is_list)
        loaded[entity] = records
        for i, record in enumerate(records):
            errors = sorted(validator.iter_errors(record), key=lambda e: e.path)
            label = f"{entity}[{i}]"
            if errors:
                for err in errors:
                    loc = "/".join(str(p) for p in err.path) or "<root>"
                    report.fail(f"{label} @ {loc}: {err.message}")
            else:
                report.ok(f"{label} valid")
    return loaded


def referential_checks(report: Report, data: dict[str, list[dict]]) -> None:
    report.section("Referential integrity")

    run = data["agent_run"][0]
    run_id = run["agent_run_id"]

    # 1. all records share one agent_run_id
    for entity, records in data.items():
        for i, rec in enumerate(records):
            if rec.get("agent_run_id") != run_id:
                report.fail(f"{entity}[{i}] agent_run_id != root run")
                break
        else:
            report.ok(f"{entity}: all records share agent_run_id")

    # 2. schema_version is uniform and expected
    versions = {
        rec.get("schema_version")
        for records in data.values()
        for rec in records
    }
    if versions == {EXPECTED_SCHEMA_VERSION}:
        report.ok(f"schema_version uniform == {EXPECTED_SCHEMA_VERSION}")
    else:
        report.fail(f"schema_version mismatch: {sorted(versions)}")

    # 3. MCP error records must carry a failure_mode (defense in depth vs schema if/then)
    for i, mcp in enumerate(data["mcp_invocation"]):
        if mcp["status"] == "error" and not mcp.get("failure_mode"):
            report.fail(f"mcp_invocation[{i}] status=error without failure_mode")
    report.ok("MCP error records carry failure_mode")

    # 4. build the id universe for decision_edge endpoint resolution
    known_ids = {
        "mcp_invocation": {m["mcp_invocation_id"] for m in data["mcp_invocation"]},
        "memory_operation": {m["memory_operation_id"] for m in data["memory_operation"]},
    }

    def endpoint_resolves(node_type: str, node_id: str) -> bool:
        if node_type in SYNTHETIC_NODE_TYPES:
            return True  # not stored as own record in V1
        if node_type == "final_answer":  # alias handled above, kept for clarity
            return True
        return node_id in known_ids.get(node_type, set())

    edges = data["decision_edge"]
    all_edges_resolve = True
    for i, edge in enumerate(edges):
        for side in ("source", "target"):
            ntype, nid = edge[f"{side}_type"], edge[f"{side}_id"]
            # final_answer points at the run id by convention
            if ntype == "final_answer" and nid == run_id:
                continue
            if not endpoint_resolves(ntype, nid):
                report.fail(f"decision_edge[{i}] {side} {ntype}:{nid} does not resolve")
                all_edges_resolve = False
    if all_edges_resolve:
        report.ok("all decision_edge endpoints resolve")

    # 5. golden causal path: stale memory -> llm_call -> final_answer
    stale_mem_ids = {
        m["memory_operation_id"]
        for m in data["memory_operation"]
        if m.get("is_stale")
    }
    if not stale_mem_ids:
        report.fail("no stale memory_operation present in demo fixture")
        return

    def has_edge(src_type, src_ids, rel_targets) -> set[str]:
        """Return target_ids reachable from any src id via an edge."""
        out = set()
        for e in edges:
            if e["source_type"] == src_type and e["source_id"] in src_ids:
                if (e["target_type"], "*") in rel_targets or (
                    e["target_type"],
                    e["relation"],
                ) in rel_targets:
                    out.add(e["target_id"])
        return out

    llm_targets = has_edge("memory_operation", stale_mem_ids, {("llm_call", "*")})
    if not llm_targets:
        report.fail("stale memory does not connect to an llm_call")
        return
    report.ok("stale memory -> llm_call edge present")

    answer_targets = has_edge("llm_call", llm_targets, {("final_answer", "*")})
    if answer_targets:
        report.ok("llm_call -> final_answer edge present (causal path complete)")
    else:
        report.fail("llm_call does not connect to final_answer")

    # 6. the demo run is recorded as an incorrect outcome (it should be wrong)
    if run.get("outcome_correct") is False:
        report.ok("demo run outcome_correct == false (expected wrong answer)")
    else:
        report.fail("demo run should have outcome_correct == false")


def main() -> int:
    print("HELIOS V1 — Phase 1 schema validation")
    print(f"  schemas:  {SCHEMA_DIR}")
    print(f"  examples: {EXAMPLES_DIR}")

    report = Report()
    registry = _build_registry()
    data = structural_checks(report, registry)

    # only run referential checks if structure is sound
    if not report.failed:
        referential_checks(report, data)

    print("\n" + "=" * 60)
    print(f"  {len(report.passed)} passed, {len(report.failed)} failed")
    print("=" * 60)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())

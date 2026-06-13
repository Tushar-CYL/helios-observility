"""HELIOS demo — "Why did the agent do that?" reproduced live through the SDK.

A support agent is asked for the refund window. It calls three mock MCP servers;
the authoritative `policy-db-mcp` times out, so the agent falls back to a STALE
cached memory (155 days old) which drives the LLM to a WRONG answer ("14 days").

Everything is deterministic and fully offline — mock MCP servers and a mock LLM,
no external API calls — so it doubles as a privacy-first, repeatable demo and an
end-to-end SDK test. Running it populates the four HELIOS tables and emits a real
distributed trace to Tempo.

    python sdk-python/examples/refund_agent.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import helios_sdk as helios
from helios_sdk.adapters import default_registry

PROTOCOL = "2025-06-18"


# --- Mock MCP servers (stand in for real MCP endpoints) --------------------
class MockMcpServer:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version

    def call(self, tool: str, arguments: dict):
        raise NotImplementedError


class KnowledgeBaseMcp(MockMcpServer):
    def call(self, tool, arguments):
        return {"hits": 2}


class CrmMcp(MockMcpServer):
    def call(self, tool, arguments):
        return {"tier": "standard"}


class PolicyDbMcp(MockMcpServer):
    def call(self, tool, arguments):
        # The authoritative source is down — this is the root trigger.
        raise TimeoutError("upstream policy-db did not respond within 5s")


def mock_llm(prompt_context: str) -> tuple[str, int, int]:
    """Deterministic 'LLM'. Returns (answer, input_tokens, output_tokens)."""
    # It trusts the (stale) cached policy and confidently states the wrong number.
    return "Our refund window is 14 days from purchase.", 412, 38


def run_demo() -> str:
    helios.init(service_name="support-assistant")
    adapter = default_registry.resolve(PROTOCOL)

    kb, crm, policy_db = (
        KnowledgeBaseMcp("knowledge-base-mcp", "1.2.0"),
        CrmMcp("crm-mcp", "0.9.4"),
        PolicyDbMcp("policy-db-mcp", "2.0.1"),
    )

    question = "What is our current refund window for online orders?"
    print(f"\nUser: {question}\n")

    with helios.agent_run(
        "support-assistant",
        framework="langgraph",
        agent_version="0.3.1",
        input=question,
    ) as run:
        # 1. knowledge base lookup (ok)
        with helios.mcp_call(
            kb.name, "search_policies", protocol_version=PROTOCOL,
            transport="http", server_version=kb.version, permission_scope="kb:read",
            arguments=adapter.encode_arguments({"query": "refund window online orders"}),
        ) as call:
            call.set_result(adapter.encode_result(kb.call("search_policies", {})))

        # 2. customer tier (ok)
        with helios.mcp_call(
            crm.name, "get_customer_tier", protocol_version=PROTOCOL,
            transport="stdio", server_version=crm.version, permission_scope="crm:read",
            arguments=adapter.encode_arguments({"customer_id": "c-8842"}),
        ) as call:
            call.set_result(adapter.encode_result(crm.call("get_customer_tier", {})))

        # 3. authoritative policy fetch — TIMES OUT (the root trigger)
        with helios.mcp_call(
            policy_db.name, "fetch_policy", protocol_version=PROTOCOL,
            transport="http", server_version=policy_db.version,
            permission_scope="policy:read", permission_changed=True,
            arguments=adapter.encode_arguments({"policy_id": "refund-window"}),
        ) as policy_call:
            try:
                policy_call.set_result(adapter.encode_result(policy_db.call("fetch_policy", {})))
                policy_ok = True
            except Exception as exc:  # noqa: BLE001 — classified, not swallowed
                mode, etype, msg = adapter.classify_error(exc)
                policy_call.fail(mode, etype, msg)
                policy_ok = False
                print(f"  ! {policy_db.name} failed: {mode} ({msg})")

        # 4. fallback to cached memory because the live fetch failed
        cached_at = datetime.now(tz=timezone.utc) - timedelta(days=155)
        with helios.memory_op(
            "read", store="redis", namespace="policy-cache", key="refund-window",
            query="refund window online orders",
            content="Refund window: 14 days from purchase.",
            source="policy-db-mcp@2026-01-10", confidence=0.81,
            created_at=cached_at, stale_after_seconds=86400,
        ) as stale_mem:
            stale_mem.set_content("Refund window: 14 days from purchase.")
            print(f"  ~ fell back to cached memory 'refund-window' (155 days old, STALE)")

        with helios.memory_op(
            "read", store="redis", namespace="session", key="customer:c-8842:tier",
            content="standard", source="crm-mcp", confidence=0.99,
            created_at=datetime.now(tz=timezone.utc) - timedelta(seconds=2),
            stale_after_seconds=86400,
        ):
            pass

        # 5. LLM produces the (wrong) answer from the stale cache
        answer, in_tok, out_tok = mock_llm("uses stale refund-window cache")
        llm = helios.llm_call("llm-refund-1")

        # 6. wire the causal path: timeout -> stale memory -> llm -> wrong answer
        helios.decision(policy_call, stale_mem, "triggered", weight=0.9)
        helios.decision(stale_mem, llm, "influenced", weight=0.95)
        helios.decision(llm, helios.final_answer(run), "caused", weight=1.0)

        # 7. finalize the run
        run.set_output(answer)
        run.set_usage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=0.0021)
        run.set_outcome(correct=False)
        print(f"\nAgent: {answer}")
        print("  (WRONG — the real window changed; the agent used a stale cache)\n")

        print(f"agent_run_id = {run.id}")
        return run.id


def _auto_triage(run_id: str, rca_url: str = "http://localhost:8088") -> None:
    """Ask the HELIOS RCA service to evaluate + auto-RCA this run.

    Best-effort: if the RCA service isn't running, the demo still succeeds — the
    Copilot panels just won't be populated until you call /triage yourself.
    """
    import json
    import urllib.error
    import urllib.request

    url = f"{rca_url}/triage/{run_id}?expected=30%20days"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        ev = body.get("evaluation", {})
        print(
            f"  triage: quality={ev.get('overall_score')} "
            f"rca_triggered={body.get('rca_triggered')}"
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        print("  triage: RCA service not reachable on :8088 (skipped) — start it with docker compose")


def main() -> int:
    run_id = run_demo()
    helios.flush()
    print("\nRunning quality gate + auto root-cause analysis...")
    _auto_triage(run_id)
    print("\nFlushed to HELIOS. Explore:")
    print("  Grafana  -> http://localhost:3000/d/helios-causal-path")
    print(f"  ClickHouse: SELECT * FROM helios.agent_run WHERE agent_run_id = '{run_id}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

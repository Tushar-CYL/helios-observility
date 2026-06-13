# HELIOS Python SDK (V1)

Thin OpenTelemetry wrapper for **Agent Runtime Intelligence**. It captures agent
runs, MCP tool calls, memory operations, and the causal **decision edges** that
connect them — then writes both a real distributed trace (OTLP → Tempo) and the
four structured HELIOS records (→ ClickHouse).

## Install (editable)

```bash
pip install -e sdk-python
```

Requires the HELIOS stack running (`docker compose up -d` in `deploy/`).

## Usage

```python
import helios_sdk as helios

helios.init(service_name="support-assistant")

with helios.agent_run("support-assistant", framework="langgraph",
                      input="What is the refund window?") as run:
    with helios.mcp_call("policy-db-mcp", "fetch_policy",
                         protocol_version="2025-06-18") as call:
        try:
            call.set_result(do_call())
        except Exception as exc:
            call.fail("timeout", type(exc).__name__, str(exc))

    with helios.memory_op("read", store="redis", key="refund-window",
                          created_at=cached_at, stale_after_seconds=86400) as mem:
        ...

    llm = helios.llm_call("llm-1")
    helios.decision(call, mem, "triggered", weight=0.9)
    helios.decision(mem, llm, "influenced", weight=0.95)
    helios.decision(llm, helios.final_answer(run), "caused")

    run.set_output("14 days"); run.set_outcome(correct=False)
```

## Design

- **OTel-native.** Every entity is a real span; correlation (`agent_run_id`,
  sequence/step indices, parent spans) is wired automatically.
- **Two sinks, one instrumentation.** A `BatchSpanProcessor` exports OTLP to the
  collector (→ Tempo); `HeliosSpanProcessor` maps finished spans into the four
  ClickHouse tables. Either can be disabled.
- **Versioned MCP adapters.** MCP wire shapes live in `adapters.py`, selected by
  `protocol_version` — never hard-coded into the core.
- **Privacy-first.** The SDK contacts no external LLM; the demo is fully offline.

## Demo

```bash
python sdk-python/examples/refund_agent.py
```

Reproduces the "Why did the agent do that?" scenario live: a multi-MCP run where
`policy-db-mcp` times out, the agent falls back to a 155-day-old stale memory,
and answers wrongly. View it at <http://localhost:3000/d/helios-causal-path>.

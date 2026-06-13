# Tutorial — Debugging a multi-MCP agent hallucination caused by stale memory

This is the canonical HELIOS walkthrough. In ~10 minutes you'll reproduce a real
class of agent failure and use HELIOS to answer the question that matters:
**"Why did the agent do that?"**

## The scenario

A customer-support agent is asked:

> *What is our current refund window for online orders?*

It confidently answers **"14 days"** — which is **wrong**. The policy changed, but
the agent never saw the update. Your job is to find out *why*.

Behind the scenes the agent:

1. queried a knowledge-base MCP server (ok),
2. looked up the customer's tier via a CRM MCP server (ok),
3. tried to fetch the authoritative policy from `policy-db-mcp` — which **timed out**,
4. silently fell back to a **155-day-old cached memory**,
5. let that stale value drive the LLM to the wrong answer.

Nothing threw an error to the user. The run's status is even `ok`. Only the
*outcome* was wrong — exactly the kind of failure traditional monitoring misses.

## Prerequisites

- Docker (with Compose) and Python 3.10+.

## Step 1 — Bring it up

```powershell
./scripts/quickstart.ps1      # Linux/macOS: ./scripts/quickstart.sh
```

This starts the stack, installs the SDK, seeds a demo run, and executes the
**live** agent (fully offline — no external LLM is called). You'll see:

```
User: What is our current refund window for online orders?

  ! policy-db-mcp failed: timeout (upstream policy-db did not respond within 5s)
  ~ fell back to cached memory 'refund-window' (155 days old, STALE)

Agent: Our refund window is 14 days from purchase.
  (WRONG — the real window changed; the agent used a stale cache)
```

## Step 2 — Open the flagship dashboard

Go to **<http://localhost:3000/d/helios-causal-path>**.

You're looking at **HELIOS · Why Did the Agent Do That?**:

- **Final Answer Outcome** turns red — `WRONG ANSWER`.
- **Root-Cause Signal** points straight at `stale: refund-window (155d)`.
- The **Causal Graph** draws the chain of nodes (MCP calls, memory reads, the
  LLM step, the final answer) connected by influence/causation edges.
- The **Resolved Causal Path** table reads, top to bottom:

  | Step | From | Relation | To |
  | --- | --- | --- | --- |
  | 0 | mcp: fetch_policy @ policy-db-mcp | triggered | memory: refund-window (STALE) |
  | 1 | memory: refund-window (STALE) | influenced | llm_call |
  | 2 | llm_call | caused | final_answer: ...14 days... |

That is the whole story in three rows: the **timeout triggered** the stale read,
which **influenced** the LLM, which **caused** the wrong answer.

## Step 3 — Confirm it in the MCP and Memory views

**MCP Observability** (`/d/helios-mcp`):

- The **tool-call chain** table shows all three calls in order. `policy-db-mcp`
  is red with `failure_mode = timeout`, and its **Perm Δ** column flags a
  permission change — a second thing worth auditing.
- **Latency by MCP Server** shows the 5 000 ms spike on `policy-db-mcp`.

**Memory Observability** (`/d/helios-memory`):

- **Stale Reads** and **Stale Read Rate** light up.
- **Memory Age by Key** shows `refund-window` towering at ~155 days, while the
  customer-tier read is fresh.

## Step 4 — See the raw trace

Every HELIOS run is also a standard OpenTelemetry trace. Grab the trace id and
open it in Tempo (via Grafana **Explore → Tempo**), or query directly:

```bash
docker exec helios-clickhouse clickhouse-client --user helios --password helios \
  --query "SELECT trace_id, output, outcome_correct FROM helios.agent_run ORDER BY start_time DESC LIMIT 1"
```

The trace contains 9 spans: the run, 3 MCP calls, 2 memory reads, and 3 decision
edges — the same data, viewable as a timeline.

## How the instrumentation works

The agent in [`sdk-python/examples/refund_agent.py`](../sdk-python/examples/refund_agent.py)
is instrumented with the HELIOS SDK. The essential shape:

```python
import helios_sdk as helios
helios.init(service_name="support-assistant")

with helios.agent_run("support-assistant", framework="langgraph", input=question) as run:
    with helios.mcp_call("policy-db-mcp", "fetch_policy",
                         protocol_version="2025-06-18") as policy_call:
        try:
            policy_call.set_result(fetch())
        except Exception as exc:
            mode, etype, msg = adapter.classify_error(exc)   # -> "timeout"
            policy_call.fail(mode, etype, msg)

    with helios.memory_op("read", store="redis", key="refund-window",
                          created_at=cached_at, stale_after_seconds=86400) as stale_mem:
        stale_mem.set_content("Refund window: 14 days from purchase.")

    llm = helios.llm_call("llm-refund-1")
    helios.decision(policy_call, stale_mem, "triggered", weight=0.9)
    helios.decision(stale_mem, llm, "influenced", weight=0.95)
    helios.decision(llm, helios.final_answer(run), "caused")

    run.set_output("Our refund window is 14 days from purchase.")
    run.set_outcome(correct=False)
```

The `decision(...)` calls are what make causal reconstruction possible — they're
the difference between *logging events* and *explaining behavior*.

## What you learned

- HELIOS captures **MCP tool-call chains** and **memory operations** as
  first-class, queryable entities.
- **Stale-memory detection** (`is_stale`, `age_ms`) surfaces a failure mode that
  conventional observability can't see.
- **Decision edges** reconstruct the causal path from cause to wrong answer.
- It's all standard **OpenTelemetry** underneath — the same run is a Tempo trace.

## Next steps

- Instrument your own agent — see the [SDK README](../sdk-python/README.md).
- Adapt a new MCP protocol version by registering an adapter in
  [`adapters.py`](../sdk-python/helios_sdk/adapters.py).
- Explore the [data model](../schema/README.md) that everything is built on.

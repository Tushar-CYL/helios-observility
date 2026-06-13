# HELIOS V1 Data Model

> The foundation of HELIOS Agent Runtime Intelligence. Every later capability
> (RCA Copilot, evals, business-outcome correlation, Digital Twin) is built on
> top of these four entities. Get this right and the rest composes cleanly.

This package is the **single source of truth** for how an agent execution is
recorded. It defines:

- The conceptual model (this document).
- [Semantic conventions](./semconv/helios-semconv.md) — the exact OpenTelemetry
  attribute names emitted on spans/events.
- [JSON Schemas](./jsonschema/) — machine-readable validation of records.
- [ClickHouse migrations](./clickhouse/migrations/) — the analytical storage DDL.
- [Example data](./examples/) — the canonical "Why did the agent do that?"
  scenario, used as a golden fixture in tests.

Current schema version: **`1.0.0`** (see `helios.schema_version`).

---

## Design goals

1. **Causality first.** The model is not just a log of events — it captures the
   *edges* between them, so we can answer "which memory or tool influenced the
   next action?" This is the moat (MCP + memory observability), not an add-on.
2. **OpenTelemetry-native.** Records map onto OTel spans/events. We reuse
   `gen_ai.*` GenAI semantic conventions where they exist and add a small,
   namespaced `helios.*` and `mcp.*` surface for what they don't yet cover.
3. **Versioned, not hard-coded.** Every record carries `helios.schema_version`.
   MCP is captured through *versioned adapters* (`mcp.protocol.version`), because
   the protocol is still evolving — we never bake one MCP shape into storage.
4. **Capture, don't manage.** Memory observability records *operations* and links
   them to spans. HELIOS is not a memory store and makes no attempt to manage,
   normalize, or semantically interpret memory backends.
5. **Privacy by construction.** Payload fields (`*.content`, `*.arguments`,
   `*.result`) are optional and pass through redaction at ingestion. The causal
   graph works even when payloads are stripped.

---

## The four entities

```
            ┌─────────────────────────────────────────────┐
            │                 agent_run                    │
            │   one logical agent execution (root span)    │
            └───────────────────────┬─────────────────────┘
                                    │ agent_run_id
        ┌────────────────────────┬──┴───────────────────────┐
        ▼                        ▼                           ▼
┌──────────────────┐   ┌────────────────────┐      ┌──────────────────┐
│ mcp_invocation   │   │ memory_operation   │      │   (llm call /     │
│ a tool call to   │   │ read/write/update/ │      │    agent step,    │
│ an MCP server    │   │ evict on a memory  │      │   via gen_ai.*)   │
└────────┬─────────┘   └─────────┬──────────┘      └─────────┬────────┘
         │                       │                           │
         └───────────┬───────────┴───────────────┬──────────┘
                     ▼                            ▼
              ┌───────────────────────────────────────┐
              │             decision_edge             │
              │  source (memory|tool|llm) ──relation──▶ │
              │  target (tool|llm|answer|step)        │
              └───────────────────────────────────────┘
```

Everything is correlated by **`agent_run_id`**. The `decision_edge` table is the
backbone that lets the Grafana plugin reconstruct the causal path:

```
stale memory_operation ──influenced──▶ llm_call ──produced──▶ wrong final answer
```

### 1. `agent_run`

One logical agent execution, spanning many spans and tool calls. Maps to the
**root span** of the run.

| Concept            | Field                       | Notes                                          |
| ------------------ | --------------------------- | ---------------------------------------------- |
| Correlation root   | `agent_run_id`              | UUID; ties every other record to this run.     |
| Trace linkage      | `trace_id`, `root_span_id`  | OTel hex IDs.                                   |
| Identity           | `agent.name`, `agent.version`, `agent.framework` | framework ∈ langgraph/autogen/crewai/custom |
| Task               | `input`, `output`           | user task / final answer (redactable).         |
| Lifecycle          | `start_time`, `end_time`, `duration_ms`, `status` | status ∈ ok/error.        |
| Usage (low-effort) | `usage.input_tokens`, `usage.output_tokens`, `usage.cost_usd` | optional. |
| Outcome (demo)     | `outcome.correct`           | optional; was the final answer right?          |
| Forward-compat     | `tenant_id`                 | defaults to `default`; reserved for V3 multi-tenancy. |

### 2. `mcp_invocation`

A single call to an MCP server. Future agents may chain 20–50 of these; the
`sequence_index` and trace parentage let us reconstruct the chain.

| Concept            | Field                                          | Notes                                   |
| ------------------ | ---------------------------------------------- | --------------------------------------- |
| Identity           | `mcp_invocation_id`                            | UUID.                                   |
| Correlation        | `agent_run_id`, `trace_id`, `span_id`, `parent_span_id` |                                |
| Server             | `mcp.server.name`, `mcp.server.version`, `mcp.transport` | transport ∈ stdio/http/sse.   |
| Protocol (adapter) | `mcp.protocol.version`                         | the MCP spec version this adapter spoke.|
| Operation          | `mcp.method`, `mcp.tool.name`                  | e.g. `tools/call`.                      |
| Payload (optional) | `mcp.tool.arguments`, `mcp.tool.result`        | JSON strings, redactable.               |
| Permissions        | `mcp.permission.scope`, `mcp.permission.changed` | audit permission shifts.             |
| Result             | `latency_ms`, `status`, `error.type`, `error.message`, `failure_mode` | status ∈ ok/error. |
| Ordering           | `sequence_index`                               | position in the call chain (0-based).   |

### 3. `memory_operation`

A read/write/update/evict against some memory backend. **Captured, not managed.**
The staleness signals (`memory.created_at`, `memory.age_ms`, `memory.is_stale`)
are what make the demo work.

| Concept            | Field                                   | Notes                                            |
| ------------------ | --------------------------------------- | ------------------------------------------------ |
| Identity           | `memory_operation_id`                   | UUID.                                            |
| Correlation        | `agent_run_id`, `trace_id`, `span_id`   |                                                  |
| Operation          | `memory.op`                             | read/write/update/evict.                         |
| Backend            | `memory.store`, `memory.namespace`      | backend name + collection (opaque to HELIOS).    |
| Target             | `memory.key`, `memory.query`            | key written/updated; query used on read.         |
| Payload (optional) | `memory.content`                        | redactable.                                      |
| Provenance         | `memory.source`, `memory.confidence`    | where it came from; retrieval score.             |
| Staleness          | `memory.created_at`, `memory.age_ms`, `memory.is_stale` | age = op_time − created_at.      |
| Result             | `latency_ms`, `status`                  |                                                  |

### 4. `decision_edge`

The causal backbone: which **source** (memory / tool / llm) influenced which
**target** (tool / llm / final answer / agent step).

| Concept       | Field                              | Notes                                                       |
| ------------- | ---------------------------------- | ----------------------------------------------------------- |
| Identity      | `decision_edge_id`                 | UUID.                                                        |
| Correlation   | `agent_run_id`, `trace_id`         |                                                             |
| Source        | `source.type`, `source.id`         | type ∈ memory_operation/mcp_invocation/llm_call.            |
| Target        | `target.type`, `target.id`         | type ∈ mcp_invocation/memory_operation/llm_call/final_answer/agent_step. |
| Relation      | `relation`                         | influenced/caused/informed/triggered.                       |
| Strength      | `weight`                           | optional 0..1 confidence/attribution.                       |
| Ordering      | `step_index`                       | logical step in the run.                                    |

---

## Versioning policy

- `helios.schema_version` follows SemVer.
- **Patch** — docs/clarification, no field changes.
- **Minor** — additive, optional fields only (backward compatible).
- **Major** — removing/renaming a field or changing a type/required-set.
- ClickHouse migrations are append-only and numbered; consumers must tolerate
  unknown fields (forward compatibility).

See [`semconv/helios-semconv.md`](./semconv/helios-semconv.md) for the exact
attribute keys and types.

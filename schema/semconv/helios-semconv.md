# HELIOS Semantic Conventions — v1.0.0

These are the **exact attribute keys** HELIOS SDKs emit on OpenTelemetry spans
and events, and that the collector maps into the ClickHouse tables. They are the
contract between instrumentation (Phase 3), ingestion (Phase 2), and storage
(Phase 1).

## Principles

- **Reuse GenAI semconv.** Where OpenTelemetry already defines an attribute
  (`gen_ai.*`), HELIOS uses it verbatim and does not redefine it.
- **Namespaces we own.** `helios.*` for run-level and cross-cutting concerns,
  `mcp.*` for Model Context Protocol, `memory.*` for memory operations,
  `decision.*` for causal edges.
- **Requirement levels** follow OTel: `Required`, `Recommended`, `Opt-In`
  (payloads/PII are Opt-In and pass through redaction).
- **Types** are the OTel attribute types: `string`, `int`, `double`, `boolean`,
  `string[]`. Timestamps are RFC 3339 strings on the wire; nanosecond ints are
  used for OTel span start/end only.

Every record carries:

| Attribute               | Type   | Level    | Notes                                  |
| ----------------------- | ------ | -------- | -------------------------------------- |
| `helios.schema_version` | string | Required | SemVer, e.g. `1.0.0`.                  |
| `helios.tenant_id`      | string | Required | `default` until V3 multi-tenancy.      |
| `helios.agent_run_id`   | string | Required | UUID correlating all records of a run. |

---

## 1. `agent_run` span

Span name: `helios.agent.run`. Span kind: `SERVER` (or `INTERNAL` for embedded).

| Attribute                    | Type    | Level       | Notes                                         |
| ---------------------------- | ------- | ----------- | --------------------------------------------- |
| `helios.agent_run_id`        | string  | Required    | UUID.                                         |
| `gen_ai.agent.name`          | string  | Required    | Reuses GenAI semconv.                         |
| `gen_ai.agent.id`            | string  | Recommended | Stable agent identifier.                      |
| `helios.agent.version`       | string  | Recommended |                                               |
| `helios.agent.framework`     | string  | Recommended | `langgraph`/`autogen`/`crewai`/`custom`.      |
| `helios.agent.input`         | string  | Opt-In      | User task; redactable.                        |
| `helios.agent.output`        | string  | Opt-In      | Final answer; redactable.                     |
| `helios.agent.status`        | string  | Required    | `ok` / `error`.                               |
| `gen_ai.usage.input_tokens`  | int     | Recommended | Reuses GenAI semconv.                         |
| `gen_ai.usage.output_tokens` | int     | Recommended | Reuses GenAI semconv.                         |
| `helios.usage.cost_usd`      | double  | Opt-In      | Enriched at ingestion if absent.              |
| `helios.outcome.correct`     | boolean | Opt-In      | Was the final answer correct (eval/feedback). |

Span timing (`start`/`end`) provides `start_time`, `end_time`, `duration_ms`.

---

## 2. `mcp_invocation` span

Span name: `mcp.<method>` (e.g. `mcp.tools/call`). Span kind: `CLIENT`.

| Attribute                 | Type   | Level       | Notes                                          |
| ------------------------- | ------ | ----------- | ---------------------------------------------- |
| `helios.agent_run_id`     | string | Required    |                                                |
| `mcp.server.name`         | string | Required    |                                                |
| `mcp.server.version`      | string | Recommended |                                                |
| `mcp.transport`           | string | Recommended | `stdio` / `http` / `sse`.                      |
| `mcp.protocol.version`    | string | Required    | MCP spec version the adapter spoke (versioned).|
| `mcp.method`              | string | Required    | e.g. `tools/call`, `resources/read`.           |
| `mcp.tool.name`           | string | Recommended | For `tools/call`.                              |
| `mcp.tool.arguments`      | string | Opt-In      | JSON string; redactable.                       |
| `mcp.tool.result`         | string | Opt-In      | JSON string; redactable.                       |
| `mcp.permission.scope`    | string | Recommended | Granted scope for this call.                   |
| `mcp.permission.changed`  | boolean| Recommended | True if scope differs from prior call.         |
| `mcp.sequence_index`      | int    | Recommended | 0-based order within the run's MCP chain.      |
| `helios.status`           | string | Required    | `ok` / `error`.                                |
| `helios.error.type`       | string | Recommended | When status=error.                             |
| `helios.error.message`    | string | Opt-In      | When status=error; redactable.                 |
| `mcp.failure_mode`        | string | Recommended | `timeout`/`permission_denied`/`server_error`/… |

Span timing provides `latency_ms`. Span/parent IDs provide chain parentage.

---

## 3. `memory_operation` event/span

Emitted as a span `memory.<op>` (e.g. `memory.read`) or a span event
`helios.memory.operation`. Span kind: `INTERNAL`.

| Attribute               | Type    | Level       | Notes                                              |
| ----------------------- | ------- | ----------- | -------------------------------------------------- |
| `helios.agent_run_id`   | string  | Required    |                                                    |
| `memory.op`             | string  | Required    | `read` / `write` / `update` / `evict`.             |
| `memory.store`          | string  | Recommended | Backend name (opaque), e.g. `redis`, `chroma`.     |
| `memory.namespace`      | string  | Recommended | Collection / namespace.                            |
| `memory.key`            | string  | Recommended | Key for write/update/evict.                        |
| `memory.query`          | string  | Opt-In      | Query text for reads; redactable.                  |
| `memory.content`        | string  | Opt-In      | Stored/retrieved content; redactable.              |
| `memory.source`         | string  | Recommended | Provenance of the memory record.                   |
| `memory.confidence`     | double  | Recommended | Retrieval score / write confidence, 0..1.          |
| `memory.created_at`     | string  | Recommended | RFC 3339; when the memory was *originally* written.|
| `memory.age_ms`         | int     | Recommended | op_time − created_at; staleness signal.            |
| `memory.is_stale`       | boolean | Recommended | Flagged by SDK/policy; drives the demo.            |
| `helios.status`         | string  | Required    | `ok` / `error`.                                    |

Span timing provides `latency_ms`.

---

## 4. `decision_edge` event

Emitted as a span event `helios.decision.edge` on the run or step span. Edges are
also exported directly to ClickHouse (they are first-class, not just span links).

| Attribute               | Type   | Level       | Notes                                              |
| ----------------------- | ------ | ----------- | -------------------------------------------------- |
| `helios.agent_run_id`   | string | Required    |                                                    |
| `decision.source.type`  | string | Required    | `memory_operation` / `mcp_invocation` / `llm_call`.|
| `decision.source.id`    | string | Required    | ID of the influencing record.                      |
| `decision.target.type`  | string | Required    | `mcp_invocation`/`memory_operation`/`llm_call`/`final_answer`/`agent_step`. |
| `decision.target.id`    | string | Required    | ID of the influenced record.                       |
| `decision.relation`     | string | Required    | `influenced`/`caused`/`informed`/`triggered`.      |
| `decision.weight`       | double | Opt-In      | Attribution strength, 0..1.                        |
| `decision.step_index`   | int    | Recommended | Logical step in the run.                           |

---

## Enumerations

| Enum                    | Allowed values                                                   |
| ----------------------- | ---------------------------------------------------------------- |
| `helios.agent.framework`| `langgraph`, `autogen`, `crewai`, `custom`                       |
| status fields           | `ok`, `error`                                                    |
| `mcp.transport`         | `stdio`, `http`, `sse`                                           |
| `mcp.failure_mode`      | `timeout`, `permission_denied`, `server_error`, `bad_response`, `cancelled` |
| `memory.op`             | `read`, `write`, `update`, `evict`                               |
| `decision.source.type`  | `memory_operation`, `mcp_invocation`, `llm_call`                 |
| `decision.target.type`  | `mcp_invocation`, `memory_operation`, `llm_call`, `final_answer`, `agent_step` |
| `decision.relation`     | `influenced`, `caused`, `informed`, `triggered`                  |

These enums are mirrored exactly in the JSON Schemas and as ClickHouse
`LowCardinality(String)` columns.

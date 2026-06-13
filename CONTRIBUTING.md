# Contributing to HELIOS

Thanks for your interest! HELIOS is an open-source Agent Runtime Intelligence
platform under [Apache-2.0](LICENSE). Contributions of all sizes are welcome.

## Project scope (V1)

V1 is intentionally focused: **OpenTelemetry pipeline + MCP observability +
memory observability + Grafana**, demonstrated by the "Why did the agent do
that?" workflow. To keep V1 shippable, the following are **out of scope for now**
(they are later releases): RCA/Copilot, evaluations, multi-tenancy, billing,
business-KPI ingestion, Digital Twin, anomaly detection, and any standalone UI
(we use Grafana). PRs that expand V1 scope may be asked to wait for the relevant
release.

## Development setup

```bash
# 1. Start the stack
cd deploy && docker compose up -d && cd ..

# 2. Install the SDK (editable) with dev extras
pip install -e "sdk-python[dev]"
```

## Verifying your changes

| Area | How to verify |
| --- | --- |
| **Data model** (`schema/`) | `python schema/tools/validate.py` — must be all green. |
| **SDK / pipeline** (`sdk-python/`) | `python sdk-python/examples/refund_agent.py`, then confirm rows land in the four `helios.*` tables and a trace reaches Tempo. |
| **Dashboards** (`deploy/grafana/`) | Restart Grafana, then check `http://localhost:3000/api/search?tag=helios`. |
| **Stack** (`deploy/`) | `docker compose config` must validate; the smoke test `python deploy/scripts/smoke_test.py` must land data in all backends. |

## Conventions

- **The data model is the contract.** If you change an entity, update *all* of:
  the spec ([`schema/README.md`](schema/README.md)), the semantic conventions,
  the JSON Schema, the ClickHouse migration, and the SDK `semconv.py`. The
  validation harness must still pass.
- **MCP is versioned, never hard-coded.** Add support for a new protocol version
  by registering a new adapter in
  [`adapters.py`](sdk-python/helios_sdk/adapters.py) — do not branch on version
  inside the core.
- **Memory is captured, not managed.** Record memory operations and link them to
  spans; HELIOS is not a memory store and does not interpret backends.
- **Privacy-first.** Nothing should require contacting an external LLM or
  sending data off the local stack. Payload fields are opt-in and redactable.
- Keep changes minimal and focused; prefer editing existing files over adding new ones.

## Submitting

1. Open an issue describing the change (especially for anything non-trivial).
2. Keep PRs scoped to one concern, with verification steps in the description.
3. Ensure the relevant checks above pass.

## Migrations

ClickHouse migrations in `schema/clickhouse/migrations/` are **append-only** and
numbered. Never edit an existing migration; add a new one. Consumers must
tolerate unknown fields (forward compatibility), and `helios.schema_version`
follows SemVer (see the versioning policy in the schema README).

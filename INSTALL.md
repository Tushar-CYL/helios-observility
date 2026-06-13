# Installing HELIOS

This guide takes you from a fresh machine to a fully running HELIOS stack — the
Agent Runtime Intelligence platform with dashboards, the AI Ops Copilot, the
evaluation engine, and business-outcome correlation.

> **TL;DR**
> ```bash
> git clone <your-repo-url> helios && cd helios
> ollama pull phi3.5
> ./scripts/quickstart.ps1          # Windows  (Linux/macOS: ./scripts/quickstart.sh)
> python deploy/scripts/seed_outcomes.py
> ```
> Then open http://localhost:3000 and log in with `helios` / `helios`.

---

## 1. Prerequisites

Install these three tools before you start.

| Tool | Why | Get it |
| --- | --- | --- |
| **Docker** (with Compose v2) | Runs the whole stack (8 containers) | <https://docs.docker.com/get-docker/> |
| **Python 3.10+** | Installs the SDK and runs the demo/seed scripts | <https://www.python.org/downloads/> |
| **Ollama** | Local LLM for the privacy-first AI Copilot & eval judge | <https://ollama.com/> |

Verify they're available:

```powershell
docker --version
docker compose version
python --version
ollama --version
```

Make sure **Docker Desktop is running** before continuing.

### Pull the local LLM model

The Copilot and the LLM-as-judge evaluator use a local model (nothing is sent to
any cloud). Pull it once:

```powershell
ollama pull phi3.5
```

> HELIOS still works without Ollama — it automatically falls back to deterministic
> heuristics — but you'll get the richest explanations with the model present.

---

## 2. Get the code

```powershell
git clone <your-repo-url> helios
cd helios
```

Everything you need is in the repo. The **data** (traces, metrics, evaluations)
is *not* in the repo — it is created locally in Docker volumes when you run the
stack, so a fresh clone always starts clean.

---

## 3. One-command setup (recommended)

The quickstart script starts the stack, waits for it to be healthy, installs the
SDK, seeds the demo, and runs a live agent.

```powershell
# Windows (PowerShell)
./scripts/quickstart.ps1
```

```bash
# Linux / macOS
./scripts/quickstart.sh
```

Then load the business-outcomes demo data:

```powershell
python deploy/scripts/seed_outcomes.py
```

Skip to [section 5](#5-open-helios) when it finishes.

---

## 4. Manual setup (if you prefer step by step)

```powershell
# 4a. Start the containerized stack
cd deploy
docker compose up -d
cd ..

# 4b. Install the Python SDK (editable)
pip install -e sdk-python

# 4c. Seed the core demo + run a live agent
python deploy/scripts/seed_demo.py --reset
python sdk-python/examples/refund_agent.py

# 4d. Seed the business-outcomes demo + correlate
python deploy/scripts/seed_outcomes.py
```

First run downloads the container images (a few minutes). Later runs are instant.

---

## 5. Open HELIOS

| Service | URL | Login |
| --- | --- | --- |
| **Grafana** (dashboards) | <http://localhost:3000> | `helios` / `helios` (via Keycloak) |
| Keycloak admin | <http://localhost:8080> | `admin` / `admin` |
| RCA Copilot API | <http://localhost:8088/healthz> | — |

In Grafana, open the **HELIOS** folder:

- **Why Did the Agent Do That?** — the flagship causal graph + AI Copilot
- **Business Outcomes** — KPI trends linked to technical drivers
- **MCP / Memory / Agent Runs** — the detail dashboards

Walk through the story in [`docs/tutorial-stale-memory.md`](docs/tutorial-stale-memory.md).

---

## 6. Ports used

| Port | Service |
| --- | --- |
| 3000 | Grafana |
| 8080 | Keycloak |
| 8088 | RCA Copilot service |
| 8123 / 9000 | ClickHouse (HTTP / native) |
| 3200 | Tempo (traces) |
| 3100 | Loki (logs) |
| 9009 | Mimir (metrics) |
| 4317 / 4318 | OpenTelemetry Collector (OTLP gRPC / HTTP) |

If a port is already in use, stop the conflicting program or change the left-hand
number in [`deploy/docker-compose.yml`](deploy/docker-compose.yml).

---

## 7. Everyday commands

```powershell
# stop everything (keeps data)
cd deploy; docker compose stop

# start again
cd deploy; docker compose up -d

# view logs for one service
docker logs helios-rca --tail 50

# wipe ALL data and start fresh (deletes Docker volumes)
cd deploy; docker compose down -v
```

---

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `docker compose` fails | Make sure Docker Desktop is **running**. |
| Grafana login loops / fails | Give Keycloak ~30s on first boot; it imports the realm on startup. |
| Copilot shows heuristic, not LLM text | Ollama isn't running or `phi3.5` isn't pulled. Run `ollama pull phi3.5`. |
| Dashboards show "No data" | Run `python deploy/scripts/seed_demo.py --reset` and `seed_outcomes.py`. |
| **Business Outcomes** dashboard empty | The dashboard ships in the repo, but its **data does not** — run `python deploy/scripts/seed_outcomes.py` (it seeds KPIs and runs the correlation automatically). Requires the RCA service on port 8088. |
| Port already in use | Change the published port in `deploy/docker-compose.yml`. |
| `pip install -e sdk-python` warnings on Windows | Safe to ignore; verify with `python -c "import helios_sdk"`. |

---

## 9. Security note (read before deploying anywhere real)

This project ships with **local-development credentials** so it runs out of the
box:

- Grafana / ClickHouse: `helios` / `helios`
- Keycloak admin: `admin` / `admin`
- A hardcoded Keycloak client secret

These are **fine for local use and demos**, but **must be changed** before any
shared or production deployment (move them to environment variables / secrets,
rotate the client secret, and disable anonymous defaults). Production hardening
(secrets management, HA, security audit) is tracked as a later phase.

#!/usr/bin/env bash
# HELIOS one-command quickstart (Linux / macOS).
# Brings up the stack, waits for health, installs the SDK, seeds, runs the demo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIMEOUT="${TIMEOUT:-120}"

step() { printf '\n==> %s\n' "$1"; }

step "Checking prerequisites"
docker version --format '{{.Server.Version}}' >/dev/null || {
  echo "Docker does not appear to be running. Start Docker and retry." >&2
  exit 1
}
python3 --version >/dev/null
echo "  Docker and Python found."

step "Starting the HELIOS stack (docker compose up -d)"
( cd "$ROOT/deploy" && docker compose up -d )

step "Waiting for ClickHouse to become healthy"
deadline=$(( $(date +%s) + TIMEOUT ))
state=""
while [ "$state" != "healthy" ] && [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 3
  state="$(docker inspect -f '{{.State.Health.Status}}' helios-clickhouse 2>/dev/null || echo starting)"
  echo "  clickhouse: $state"
done
[ "$state" = "healthy" ] || { echo "ClickHouse not healthy within ${TIMEOUT}s." >&2; exit 1; }

if [ "${SKIP_SDK_INSTALL:-0}" != "1" ]; then
  step "Installing the HELIOS SDK (editable)"
  python3 -m pip install -e "$ROOT/sdk-python" --quiet
  python3 -c "import helios_sdk" && echo "  helios_sdk installed."
fi

step "Seeding the demo fixture"
python3 "$ROOT/deploy/scripts/seed_demo.py" --reset

step "Running the live demo agent (offline, no external LLM)"
python3 "$ROOT/sdk-python/examples/refund_agent.py"

cat <<'EOF'

HELIOS is ready.
  Grafana   : http://localhost:3000 (HELIOS folder)
  Flagship  : http://localhost:3000/d/helios-causal-path
  Tutorial  : docs/tutorial-stale-memory.md
EOF

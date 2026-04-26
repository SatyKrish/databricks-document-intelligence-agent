#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-${DOCINTEL_TARGET:-demo}}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "no python interpreter found (.venv/bin/python or python3)" >&2
  exit 1
fi

"$PYTHON" - "$TARGET" <<'PY'
import sys
from databricks.sdk import WorkspaceClient

target = sys.argv[1]
display_name = f"doc-intel-supervisor-{target}"
w = WorkspaceClient()
for agent in w.supervisor_agents.list_supervisor_agents():
    if agent.display_name == display_name and agent.endpoint_name:
        print(agent.endpoint_name)
        raise SystemExit(0)
raise SystemExit(f"Agent Bricks Supervisor endpoint not found for target {target!r}")
PY

#!/usr/bin/env bash
# ./run.sh <SIM_URL> — starts the controller and runs until the scenario finishes. No manual steps.
# Uses ./.venv if present, else creates it from requirements.txt (no dependencies beyond the simulator's own).
# State lives in ${CONTROLLER_DB:-state/controller.db}; a new world (different scenario or a restarted
# simulator) is detected and gets a fresh store, the old one is archived next to it.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m controller --db "${CONTROLLER_DB:-state/controller.db}" run "$SIM_URL"

"""Fault injection for the crash test. Off unless CONTROLLER_FAULTS is set.

CONTROLLER_FAULTS="approval_after_intent,apply_after_post" makes the process die (os._exit, no cleanup) the
first time it reaches each named point. A marker file next to the DB makes every point fire only once, so
the restarted controller gets past it. This lets the test hit the exact windows a random kill rarely lands
in: after we wrote the intent but before the HTTP call, and after the simulator acted but before we wrote
down its answer.
"""
import os
from pathlib import Path

_POINTS = {p.strip() for p in os.environ.get("CONTROLLER_FAULTS", "").split(",") if p.strip()}
_DIR = Path(os.environ.get("CONTROLLER_DB", "state/controller.db")).parent


def maybe_crash(point: str):
    if point not in _POINTS:
        return
    marker = _DIR / f".fault_fired_{point}"
    if marker.exists():
        return
    marker.write_text("1")
    print(f"FAULT INJECTED: dying at {point}", flush=True)
    os._exit(137)

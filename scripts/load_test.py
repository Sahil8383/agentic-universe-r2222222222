"""Load test (scale envelope): run the controller on public_03_load and report the numbers SRE asked for.

    python scripts/load_test.py [--scenario scenarios/public_03_load.yaml]

Measures, from the controller's own metrics table:
- ingest throughput (events/s) and total ingest time, history and live separately;
- decision lag: sim minutes between the newest event ingested and "now" at decision time;
- wall-clock per simulated day (SRE's bar: one sim day of public_03 in < 10 minutes);
- peak resident memory of the controller process;
and writes reports/load_public_03.md. The simulator's own time is reported separately: it shares the box.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/public_03_load.yaml")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--out", default="reports/load_public_03.md")
    a = ap.parse_args()
    name = Path(a.scenario).stem
    run_dir, db = ROOT / "runs" / f"mine_{name}", ROOT / "state" / f"load_{name}.db"
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        p.unlink(missing_ok=True)
    t0 = time.time()
    sim = subprocess.Popen([PY, "-m", "sim", "serve", "--scenario", a.scenario, "--run-dir", str(run_dir), "--force",
                            "--port", str(a.port)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        http = httpx.Client(base_url=f"http://127.0.0.1:{a.port}", timeout=60)
        while True:
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.5)
        t_sim_ready = time.time() - t0
        t1 = time.time()
        rc = subprocess.run([str(ROOT / "run.sh"), f"http://127.0.0.1:{a.port}"], cwd=ROOT,
                            env={**os.environ, "CONTROLLER_DB": str(db)}).returncode
        wall = time.time() - t1
        st = http.get("/sim/state").json()
    finally:
        sim.terminate()
        sim.wait()
    con = sqlite3.connect(db)
    q = lambda sql: con.execute(sql).fetchone()
    days = st["end_minute"] / 1440
    hist_events = q("SELECT events FROM metrics ORDER BY minute LIMIT 1")[0]
    hist_s = q("SELECT ingest_s FROM metrics ORDER BY minute LIMIT 1")[0]
    live = q("SELECT SUM(events), SUM(ingest_s), SUM(cycle_s), COUNT(*) FROM metrics WHERE minute > 0")
    lag = q("SELECT MAX(lag_min), AVG(lag_min) FROM metrics WHERE minute > 0 AND lag_min IS NOT NULL")
    rss = q("SELECT MAX(rss_mb) FROM metrics")[0]
    slow = q("SELECT MAX(cycle_s) FROM metrics WHERE minute > 0")[0]
    per_day = con.execute("SELECT minute / 1440 AS d, SUM(events), SUM(cycle_s) FROM metrics WHERE minute > 0 "
                          "GROUP BY d ORDER BY d").fetchall()
    db_mb = sum(p.stat().st_size for p in db.parent.glob(db.name + "*")) / 1e6
    lines = [f"# Load test — {name}", "",
             f"`python scripts/load_test.py --scenario {a.scenario}` on {os.uname().sysname} "
             f"{os.uname().machine}, {os.cpu_count()} CPUs; simulator and controller on the same machine.", "",
             f"- controller exit code: {rc}; finished: {st['finished']}",
             f"- simulated days: {days:.0f}; wall-clock for the whole run (sim + controller): {wall:.0f} s "
             f"→ **{wall / days:.0f} s per simulated day** (SRE's bar: < 600 s)",
             f"- history backfill: {hist_events:,} events ingested in {hist_s:.1f} s "
             f"(**{hist_events / max(hist_s, 1e-9):,.0f} events/s**)",
             f"- live: {live[0]:,} events over {live[3]} cycles; ingest {live[1]:.1f} s "
             f"(**{live[0] / max(live[1], 1e-9):,.0f} events/s**), controller time per cycle incl. decisions "
             f"{live[2] / max(live[3], 1):.2f} s avg, {slow:.2f} s worst",
             f"- decision lag (newest event ingested → decision): max {lag[0]} sim minutes, avg {lag[1]:.1f} "
             "(events are at most one tick old when we decide)",
             f"- controller peak RSS: **{rss:.0f} MB**; SQLite store on disk: {db_mb:.0f} MB",
             f"- simulator start-up (world + history generation): {t_sim_ready:.0f} s", "",
             "| sim day | events | controller seconds |", "|---|---|---|"]
    lines += [f"| D{d} | {e:,} | {c:.1f} |" for d, e, c in per_day]
    Path(ROOT / a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

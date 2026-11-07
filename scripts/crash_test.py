"""Crash-recovery test (C13): kill -9 the controller mid-run, restart it, and prove the run ends correctly.

    python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml [--kills "D2 10:30,D3 13:00,D5 15:15"]

It starts the simulator, starts ./run.sh, and SIGKILLs the controller the moment the simulator reaches each
kill time (no shutdown hooks, no flush: the worst case). Then it restarts ./run.sh and carries on. At the end
it checks the invariants that matter:
  1. every approval the simulator holds is known to our store (nothing lost, nothing sent twice);
  2. no approval was left approved-but-unapplied, and none lapsed because of a crash;
  3. every approval was applied at most once;
  4. ingestion is exactly-once: calls counted in our store == call events in the simulator's chunks;
  5. the scenario finished and the grade report has no harmful changes caused by the restarts.
This script is a test harness: it may read run dirs and start the simulator. The controller never does.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable


def minute(s: str) -> int:
    d, hm = s.split()
    h, m = hm.split(":")
    return int(d[1:]) * 1440 + int(h) * 60 + int(m)


def main():
    sim_box = []
    try:
        run(sim_box)
    finally:
        for p in sim_box:  # never leave a simulator holding the port
            if p.poll() is None:
                p.terminate()
                p.wait()


def run(sim_box: list):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/public_02_incidents.yaml")
    ap.add_argument("--kills", default="D2 10:30,D3 13:00,D5 15:15")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--out", default="reports/crash_recovery.md")
    ap.add_argument("--faults", default="approval_after_intent,approval_after_post,apply_before_post,apply_after_post,"
                                        "auto_after_intent,auto_after_put,ingest_before_commit",
                    help="named crash points (see controller/faults.py); each fires once, then we restart")
    a = ap.parse_args()
    name = Path(a.scenario).stem
    run_dir, db = ROOT / "runs" / f"crash_{name}", ROOT / "state" / f"crash_{name}.db"
    for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm"), *db.parent.glob(".fault_fired_*")):
        p.unlink(missing_ok=True)
    url = f"http://127.0.0.1:{a.port}"
    log = []

    def say(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log.append(line)

    sim = subprocess.Popen([PY, "-m", "sim", "serve", "--scenario", a.scenario, "--run-dir", str(run_dir),
                            "--force", "--port", str(a.port)], cwd=ROOT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    sim_box.append(sim)
    http = httpx.Client(base_url=url, timeout=60)
    for _ in range(300):
        try:
            if http.get("/health").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.2)
    env = {**os.environ, "CONTROLLER_DB": str(db), "CONTROLLER_FAULTS": a.faults}

    def start():
        return subprocess.Popen([str(ROOT / "run.sh"), url], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)

    kills = [minute(k.strip()) for k in a.kills.split(",") if k.strip()]
    ctl = start()
    say(f"started controller (pid {ctl.pid}) against {a.scenario}")
    for k in kills:
        while True:
            st = http.get("/sim/state").json()
            if st["finished"] or st["sim_minute"] >= k:
                break
            if ctl.poll() is not None:
                if ctl.returncode != 137:
                    raise SystemExit(f"controller crashed: {ctl.stderr.read().decode()[-2000:]}")
                say(f"controller died at an injected fault point (sim {st['ts']}); restarting")
                ctl = start()
            time.sleep(0.02)
        os.killpg(ctl.pid, signal.SIGKILL)
        ctl.wait()
        con = sqlite3.connect(db)
        states = dict(con.execute("SELECT state, COUNT(*) FROM proposals GROUP BY state").fetchall())
        last_chunk = con.execute("SELECT value FROM meta WHERE key='last_chunk'").fetchone()
        con.close()
        say(f"kill -9 at sim {st['ts']} (sim minute {st['sim_minute']}); store had proposals {states}, "
            f"last_chunk {last_chunk[0] if last_chunk else None}")
        ctl = start()
        say(f"restarted controller (pid {ctl.pid})")
    while True:  # injected faults make the controller die on its own: restart until it finishes cleanly
        rc = ctl.wait()
        if rc == 0:
            break
        fired = sorted(p.name.replace(".fault_fired_", "") for p in db.parent.glob(".fault_fired_*"))
        st = http.get("/sim/state").json()
        say(f"controller died (exit {rc}) at sim {st['ts']}; faults fired so far: {fired}")
        ctl = start()
        say(f"restarted controller (pid {ctl.pid})")
    say(f"controller exited with code {rc}")

    # ---------------- invariants
    st = http.get("/sim/state").json()
    con = sqlite3.connect(db)
    ours = {r[0]: r[1] for r in con.execute("SELECT approval_id, state FROM proposals WHERE approval_id IS NOT NULL")}
    checks = []
    wfs = [w["workflow_id"] for w in http.get("/workflows").json()]
    remote = [x for w in wfs for x in http.get("/approvals", params={"workflow_id": w}).json()]
    missing = [x["approval_id"] for x in remote if x["approval_id"] not in ours]
    checks.append(("every simulator approval is in our store", not missing, f"{len(remote)} approvals, missing {missing}"))
    dup_pids = len(remote) - len({x["proposal_id"] for x in remote})
    checks.append(("no proposal sent twice", dup_pids == 0, f"{dup_pids} duplicates"))
    # an approval the simulator still shows as "approved" is fine only if we tried to apply it and got
    # 409 stale_base_version (someone changed the config after we proposed); we then re-propose on top
    stuck = [x["approval_id"] for x in remote if x["status"] == "approved" and ours.get(x["approval_id"]) != "stale"]
    stale = [x["approval_id"] for x in remote if x["status"] == "approved" and ours.get(x["approval_id"]) == "stale"]
    lapsed = [x["approval_id"] for x in remote if x["status"] == "lapsed"]
    checks.append(("no approval left approved-but-unapplied", not stuck,
                   f"stuck {stuck}; correctly stale after a supervisor edit: {stale}"))
    checks.append(("no approval lapsed", not lapsed, f"{lapsed}"))
    applied_ids = [v["approval_id"] for w in wfs for v in http.get(f"/workflows/{w}/config/history").json()
                   if v["approval_id"]]
    checks.append(("each approval applied at most once", len(applied_ids) == len(set(applied_ids)),
                   f"{len(applied_ids)} applies"))
    calls_store = con.execute("SELECT COALESCE(SUM(dials),0) FROM counters WHERE key='ALL'").fetchone()[0]
    calls_sim = 0
    for ch in http.get("/events/chunks", params={"kind": "history"}).json() + \
            http.get("/events/chunks", params={"kind": "events"}).json():
        calls_sim += http.get(f"/events/chunks/{ch['chunk']}").text.count('"type":"call"')
    checks.append(("exactly-once ingestion (calls in store == call events in chunks)", calls_store == calls_sim,
                   f"store {calls_store} vs simulator {calls_sim}"))
    checks.append(("scenario finished", st["finished"], st["ts"]))
    sim.terminate()
    sim.wait()
    base = ROOT / "runs" / f"base_{name}"
    if not base.exists():
        subprocess.run([PY, "-m", "sim", "baseline", "--scenario", a.scenario, "--run-dir", str(base)], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL)
    g = json.loads(subprocess.run([PY, "-m", "sim", "grade", "--run", str(run_dir), "--baseline", str(base), "--json"],
                                  cwd=ROOT, check=True, capture_output=True, text=True).stdout)
    verdicts = g["controller_changes"]["verdicts"]
    checks.append(("no harmful changes", not verdicts.get("harmful"), json.dumps(verdicts)))
    ok = all(c[1] for c in checks)
    lines = [f"# Crash-recovery test — {name}", "",
             f"`python scripts/crash_test.py --scenario {a.scenario} --kills \"{a.kills}\"`", "",
             "The controller is killed with SIGKILL (no cleanup) the moment the simulator reaches each kill time, then "
             "restarted with the same `./run.sh`.", "", "## Log", "", "```", *log, "```", "", "## Invariants", "",
             "| check | result | detail |", "|---|---|---|"]
    lines += [f"| {c} | {'PASS' if r else 'FAIL'} | {d} |" for c, r, d in checks]
    k = g["kpis"]["ALL"]
    d = g["delta_vs_baseline"]["ALL"]
    lines += ["", f"Outcome with the crashes: conclusion rate {k['conclusion_rate']:.3f} ({d['conclusion_rate']:+.1%} vs "
                  f"baseline), {g['controller_changes']['count']} changes applied {json.dumps(verdicts)}.",
              "", f"**Overall: {'PASS' if ok else 'FAIL'}**"]
    Path(ROOT / a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ROOT / a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-len(checks) - 6:]))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

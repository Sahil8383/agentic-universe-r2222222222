"""SQLite store: the controller's memory. If it isn't in here, it didn't happen.

One file, WAL mode, one writer (the loop). Every multi-row change goes through `tx()`, so a crash leaves
either all of it or none of it. The schema maps one-to-one onto Postgres if this ever leaves a laptop.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- policy snapshot of every workflow, as GET /workflows returned it
CREATE TABLE IF NOT EXISTS workflows (wid TEXT PRIMARY KEY, tenant TEXT, spec_json TEXT);

-- the feature join: call events carry lead_id only, features come from lead events
CREATE TABLE IF NOT EXISTS feature_codes (code INTEGER PRIMARY KEY, features_json TEXT);
CREATE TABLE IF NOT EXISTS leads (wid TEXT, idx INTEGER, code INTEGER, day INTEGER,
                                  PRIMARY KEY (wid, idx)) WITHOUT ROWID;

-- marginal counters: (workflow, day, key) -> dials / connected / engaged / flagged
CREATE TABLE IF NOT EXISTS counters (wid TEXT, day INTEGER, key TEXT,
    dials INTEGER, connected INTEGER, engaged INTEGER, flagged INTEGER,
    PRIMARY KEY (wid, day, key)) WITHOUT ROWID;

-- call log (from D0 on): answers "what did this person hear, when, under which config version"
CREATE TABLE IF NOT EXISTS calls (event_id INTEGER PRIMARY KEY, wid TEXT, minute INTEGER, lead INTEGER,
    person TEXT, version INTEGER, cohort TEXT, wm TEXT, voice TEXT, did TEXT, prompt TEXT, outcome INTEGER);
CREATE INDEX IF NOT EXISTS calls_wid_minute ON calls (wid, minute);

-- every config version we know about; config_json is null when we never saw that exact config
CREATE TABLE IF NOT EXISTS config_versions (wid TEXT, version INTEGER, actor TEXT, minute INTEGER,
    changed_dims TEXT, proposal_id TEXT, approval_id TEXT, config_json TEXT, PRIMARY KEY (wid, version));

-- the proposal state machine, one row per proposal_id
CREATE TABLE IF NOT EXISTS proposals (proposal_id TEXT PRIMARY KEY, wid TEXT, kind TEXT, path TEXT,
    state TEXT, base_version INTEGER, config_json TEXT, body_json TEXT, approval_id TEXT,
    created_minute INTEGER, submitted_minute INTEGER, decided_minute INTEGER, applied_version INTEGER,
    reasons_json TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS proposals_wid_state ON proposals (wid, state);

-- the audit log: one row per thing the controller noticed, decided or did
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, minute INTEGER, wall_ts REAL,
    wid TEXT, kind TEXT, proposal_id TEXT, approval_id TEXT, version INTEGER, summary TEXT, evidence_json TEXT);
CREATE INDEX IF NOT EXISTS decisions_wid_minute ON decisions (wid, minute);

-- small JSON blobs of loop state: desired config, experiment state, incidents, detector memory
CREATE TABLE IF NOT EXISTS state (wid TEXT, key TEXT, value_json TEXT, PRIMARY KEY (wid, key));

-- validation runs we paid for (reusable as evidence for the same workflow + prompt)
CREATE TABLE IF NOT EXISTS validations (run_id TEXT PRIMARY KEY, wid TEXT, prompt TEXT, n INTEGER,
    pass_rate REAL, minute INTEGER);

-- loop health, one row per cycle
CREATE TABLE IF NOT EXISTS metrics (minute INTEGER PRIMARY KEY, wall_ts REAL, events INTEGER,
    ingest_s REAL, cycle_s REAL, lag_min INTEGER, decisions INTEGER, credits REAL, rss_mb REAL);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL: a commit survives a process kill
        self.db.executescript(SCHEMA)
        self._in_tx = False

    @contextmanager
    def tx(self):
        """All-or-nothing block. Nested calls join the outer transaction."""
        if self._in_tx:
            yield self.db
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False

    def q(self, sql: str, *args) -> list[tuple]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, *args):
        r = self.db.execute(sql, args).fetchone()
        return r[0] if r else None

    # ---- meta and state
    def meta(self, key: str, default=None):
        v = self.one("SELECT value FROM meta WHERE key=?", key)
        return default if v is None else json.loads(v)

    def set_meta(self, key: str, value):
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))

    def state(self, wid: str, key: str, default=None):
        v = self.one("SELECT value_json FROM state WHERE wid=? AND key=?", wid, key)
        return default if v is None else json.loads(v)

    def set_state(self, wid: str, key: str, value):
        self.db.execute("INSERT INTO state(wid,key,value_json) VALUES(?,?,?) "
                        "ON CONFLICT(wid,key) DO UPDATE SET value_json=excluded.value_json",
                        (wid, key, json.dumps(value, sort_keys=True)))

    # ---- audit
    def decide(self, minute: int, wid: str | None, kind: str, summary: str, evidence=None,
               proposal_id=None, approval_id=None, version=None):
        self.db.execute("INSERT INTO decisions(minute,wall_ts,wid,kind,proposal_id,approval_id,version,summary,"
                        "evidence_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (minute, time.time(), wid, kind, proposal_id, approval_id, version, summary,
                         json.dumps(evidence, sort_keys=True, default=str) if evidence is not None else None))

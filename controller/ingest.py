"""Event ingestion (C1): chunks in, counters + call log out, exactly once.

The rule that makes it crash-safe: a chunk's effects (new leads, counter deltas, call-log rows) and
`last_chunk = n` are written in ONE transaction. Kill the process anywhere and the chunk is either fully
counted or not at all; on restart we simply re-read every chunk after `last_chunk`.

Counters are marginal, not a full cross-product: per call we bump ~20 keys such as
    "A|voice=V2"                        engagement of an option, whole workflow
    "AS|lang=ta|voice=V2"               the same, inside one segment (a single feature value)
    "HS|age_band=60+|9"                 pickup of a segment in one hour of the day (for call-time tables)
    "D|D1|9"                            pickup of a caller ID in one hour
    "P|P1"                              hallucination flags under a prompt
That's enough for every decision the controller makes, and stays a few hundred keys per workflow per day.
"""
from __future__ import annotations

import json
import time
from array import array
from collections import defaultdict
from datetime import datetime

from .client import SimClient
from .faults import maybe_crash
from .store import Store

UNKNOWN = 0xFFFF  # "no lead event seen for this index" in the feature-code array
MIN_PER_DAY = 1440


class Counters:
    """In-memory copy of the `counters` table: {(wid, day): {key: [dials, connected, engaged, flagged]}}."""

    def __init__(self):
        self.by_day: dict[tuple[str, int], dict[str, list[int]]] = defaultdict(dict)

    def add(self, wid: str, day: int, key: str, row):
        cur = self.by_day[(wid, day)].get(key)
        if cur is None:
            self.by_day[(wid, day)][key] = list(row)
        else:
            for i in range(4):
                cur[i] += row[i]

    def get(self, wid: str, days, key: str) -> list[int]:
        out = [0, 0, 0, 0]
        for d in days:
            r = self.by_day.get((wid, d), {}).get(key)
            if r:
                for i in range(4):
                    out[i] += r[i]
        return out

    def prefix(self, wid: str, days, prefix: str) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for d in days:
            for k, r in self.by_day.get((wid, d), {}).items():
                if k.startswith(prefix):
                    acc = out.setdefault(k, [0, 0, 0, 0])
                    for i in range(4):
                        acc[i] += r[i]
        return out

    def days(self, wid: str) -> list[int]:
        return sorted(d for (w, d) in self.by_day if w == wid)


class Ingestor:
    def __init__(self, store: Store, client: SimClient):
        self.store, self.client = store, client
        self.counters = Counters()
        self.lead_codes: dict[str, array] = {}       # wid -> feature code per lead index
        self.lead_fallback: dict[str, dict[str, int]] = defaultdict(dict)  # if a lead_id has an odd format
        self.code_of: dict[tuple, int] = {}          # features tuple -> small int code
        self.fv_of: list[tuple[str, ...]] = []       # code -> ("lang=hi", "city_tier=2", ...)
        self.epoch0: float | None = store.meta("epoch0")
        self._ts_cache: dict[str, int] = {}
        self.unknown_lead_calls = 0
        self._load()

    # ---------------------------------------------------------------- restart
    def _load(self):
        for code, fj in self.store.q("SELECT code, features_json FROM feature_codes ORDER BY code"):
            feats = tuple(tuple(kv) for kv in json.loads(fj))  # JSON gives lists; dict keys need tuples
            self.code_of[feats] = code
            self.fv_of.append(tuple(f"{k}={v}" for k, v in feats))
        for wid, idx, code, _day in self.store.q("SELECT wid, idx, code, day FROM leads"):
            if idx < 0:
                continue
            self._set_code(wid, idx, code)
        for wid, day, key, d, c, e, f in self.store.q("SELECT * FROM counters"):
            self.counters.by_day[(wid, day)][key] = [d, c, e, f]

    def set_clock(self, sim_minute: int, ts: str):
        """D0 00:00 in wall-clock seconds, derived from the API (never from a scenario file)."""
        if self.epoch0 is None:
            self.epoch0 = datetime.fromisoformat(ts).timestamp() - sim_minute * 60
            self.store.set_meta("epoch0", self.epoch0)

    def minute(self, ts: str) -> int:
        m = self._ts_cache.get(ts)
        if m is None:
            if len(self._ts_cache) > 100_000:
                self._ts_cache.clear()
            m = int((datetime.fromisoformat(ts).timestamp() - self.epoch0) // 60)
            self._ts_cache[ts] = m
        return m

    # ---------------------------------------------------------------- lead join
    def _set_code(self, wid: str, idx: int, code: int):
        arr = self.lead_codes.get(wid)
        if arr is None:
            arr = self.lead_codes[wid] = array("H")
        if idx >= len(arr):
            arr.extend([UNKNOWN] * (idx + 1 - len(arr) + 1024))
        arr[idx] = code

    @staticmethod
    def lead_index(lead_id: str) -> int:
        """'nw_renewal:l00012345' -> 12345, or -1 if the id doesn't look like that."""
        i = lead_id.rfind(":l")
        tail = lead_id[i + 2:] if i >= 0 else ""
        return int(tail) if tail.isdigit() else -1

    def code_for_lead(self, wid: str, lead_id: str) -> int:
        idx = self.lead_index(lead_id)
        if idx >= 0:
            arr = self.lead_codes.get(wid)
            return arr[idx] if arr is not None and idx < len(arr) else UNKNOWN
        return self.lead_fallback[wid].get(lead_id, UNKNOWN)

    def features(self, code: int) -> tuple[str, ...]:
        return self.fv_of[code] if code != UNKNOWN else ()

    # ---------------------------------------------------------------- main entry
    def run(self) -> dict:
        """Ingest every chunk after the checkpoint. Returns what the loop needs to react to."""
        last = self.store.meta("last_chunk", -1)
        chunks = self.client.get("/events/chunks", kind="history", after=last) + \
            self.client.get("/events/chunks", kind="events", after=last)
        chunks.sort(key=lambda c: c["chunk"])
        out = {"events": 0, "calls": 0, "config_events": [], "approval_events": [], "max_minute": None,
               "seconds": 0.0}
        t0 = time.perf_counter()
        for ch in chunks:
            self._ingest_chunk(ch, out)
        out["seconds"] = time.perf_counter() - t0
        return out

    def _ingest_chunk(self, ch: dict, out: dict):
        n, history = ch["chunk"], ch["kind"] == "history"
        lines = self.client.iter_chunk_lines(n)
        delta: dict[tuple[str, int], dict[str, list[int]]] = defaultdict(dict)
        new_codes, lead_rows, call_rows, cfg_rows = [], [], [], []
        loads, minute_of, code_of_lead = json.loads, self.minute, self.code_for_lead
        n_calls = 0
        max_minute = None

        def bump(d: dict, key: str, conn: int, eng: int, flag: int):
            r = d.get(key)
            if r is None:
                d[key] = [1, conn, eng, flag]
            else:
                r[0] += 1
                r[1] += conn
                r[2] += eng
                r[3] += flag

        for line in lines:
            if not line:
                continue
            ev = loads(line)
            typ = ev["type"]
            if typ == "call":
                n_calls += 1
                wid = ev["workflow_id"]
                minute = minute_of(ev["ts"])
                day, hour = minute // MIN_PER_DAY, (minute % MIN_PER_DAY) // 60
                v = ev["variant"]
                wm, vo, did, pv = v["welcome_message"], v["voice"], v["caller_id"], v["prompt_version"]
                conn, eng = int(ev["connected"]), int(ev["engaged"])
                qa = ev.get("qa")
                flag = int(bool(qa and qa.get("hallucination_flag")))
                d = delta[(wid, day)]
                bump(d, "ALL", conn, eng, flag)
                bump(d, f"H|{hour}", conn, eng, flag)
                bump(d, f"D|{did}|{hour}", conn, eng, flag)
                bump(d, f"A|welcome_message={wm}", conn, eng, flag)
                bump(d, f"A|voice={vo}", conn, eng, flag)
                bump(d, f"X|{wm}|{vo}", conn, eng, flag)
                bump(d, f"P|{pv}", conn, eng, flag)
                code = code_of_lead(wid, ev["lead_id"])
                if code == UNKNOWN:
                    self.unknown_lead_calls += 1
                else:
                    for fv in self.fv_of[code]:
                        bump(d, f"HS|{fv}|{hour}", conn, eng, flag)
                        bump(d, f"AS|{fv}|welcome_message={wm}", conn, eng, flag)
                        bump(d, f"AS|{fv}|voice={vo}", conn, eng, flag)
                if not history:
                    call_rows.append((ev["event_id"], wid, minute, self.lead_index(ev["lead_id"]), ev["person_id"],
                                      ev["config_version"], ev["cohort_id"], wm, vo, did, pv,
                                      conn | (eng << 1) | (flag << 2)))
                max_minute = minute if max_minute is None or minute > max_minute else max_minute
            elif typ == "lead":
                wid = ev["workflow_id"]
                feats = tuple(sorted(ev["features"].items()))
                code = self.code_of.get(feats)
                if code is None:
                    code = len(self.fv_of)
                    self.code_of[feats] = code
                    self.fv_of.append(tuple(f"{k}={v}" for k, v in feats))
                    new_codes.append((code, json.dumps(feats)))
                minute = minute_of(ev["ts"])
                day = minute // MIN_PER_DAY
                idx = self.lead_index(ev["lead_id"])
                if idx >= 0:
                    self._set_code(wid, idx, code)
                else:
                    self.lead_fallback[wid][ev["lead_id"]] = code
                lead_rows.append((wid, idx, code, day))
                d = delta[(wid, day)]
                bump(d, "U|ALL", 0, 0, 0)
                for fv in self.fv_of[code]:
                    bump(d, f"U|{fv}", 0, 0, 0)
            elif typ == "config_changed":
                minute = minute_of(ev["ts"])
                cfg_rows.append((ev["workflow_id"], ev["version"], ev["actor"], minute,
                                 json.dumps(ev.get("changed_dims") or []), ev.get("proposal_id"),
                                 ev.get("approval_id")))
                out["config_events"].append({**ev, "minute": minute})
            elif typ == "approval_decided":
                out["approval_events"].append(ev)
            out["events"] += 1

        if not history:
            maybe_crash("ingest_before_commit")
        with self.store.tx() as db:
            if new_codes:
                db.executemany("INSERT OR IGNORE INTO feature_codes(code, features_json) VALUES(?,?)", new_codes)
            if lead_rows:
                db.executemany("INSERT OR REPLACE INTO leads(wid, idx, code, day) VALUES(?,?,?,?)",
                               [r for r in lead_rows if r[1] >= 0])
            if call_rows:
                db.executemany("INSERT OR IGNORE INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", call_rows)
            for r in cfg_rows:  # keep any config_json we already stored for our own applies
                db.execute("INSERT INTO config_versions(wid,version,actor,minute,changed_dims,proposal_id,"
                           "approval_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(wid,version) DO UPDATE SET "
                           "actor=excluded.actor, minute=excluded.minute, changed_dims=excluded.changed_dims",
                           r)
            rows = [(wid, day, k, *r) for (wid, day), keys in delta.items() for k, r in keys.items()]
            db.executemany("INSERT INTO counters VALUES(?,?,?,?,?,?,?) ON CONFLICT(wid,day,key) DO UPDATE SET "
                           "dials=dials+excluded.dials, connected=connected+excluded.connected, "
                           "engaged=engaged+excluded.engaged, flagged=flagged+excluded.flagged", rows)
            self.store.set_meta("last_chunk", n)
            self.store.set_meta("last_event_id", ch.get("last_event_id"))
        # only after the commit do the in-memory counters move, so memory never runs ahead of disk
        for (wid, day), keys in delta.items():
            for k, r in keys.items():
                self.counters.add(wid, day, k, r)
        out["calls"] += n_calls
        if max_minute is not None:
            out["max_minute"] = max(out["max_minute"] or max_minute, max_minute)

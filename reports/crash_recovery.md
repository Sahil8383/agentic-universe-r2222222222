# Crash-recovery test — public_02_incidents

`python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml --kills "D2 10:30,D3 13:00,D5 15:15"`

The controller is killed with SIGKILL (no cleanup) the moment the simulator reaches each kill time, then restarted with the same `./run.sh`.

## Log

```
[12:33:36] started controller (pid 9195) against scenarios/public_02_incidents.yaml
[12:33:38] controller died at an injected fault point (sim 2027-01-04T00:00:00+05:30); restarting
[12:33:38] controller died at an injected fault point (sim 2027-01-04T00:00:00+05:30); restarting
[12:33:38] controller died at an injected fault point (sim 2027-01-04T01:00:00+05:30); restarting
[12:33:39] controller died at an injected fault point (sim 2027-01-04T10:00:00+05:30); restarting
[12:33:39] controller died at an injected fault point (sim 2027-01-04T10:00:00+05:30); restarting
[12:33:40] controller died at an injected fault point (sim 2027-01-04T12:45:00+05:30); restarting
[12:33:40] controller died at an injected fault point (sim 2027-01-04T12:45:00+05:30); restarting
[12:33:42] kill -9 at sim 2027-01-06T10:30:00+05:30 (sim minute 3510); store had proposals {'applied': 9, 'rejected': 1}, last_chunk 106
[12:33:42] restarted controller (pid 9298)
[12:33:44] kill -9 at sim 2027-01-07T13:00:00+05:30 (sim minute 5100); store had proposals {'applied': 12, 'pending': 1, 'rejected': 1, 'stale': 1}, last_chunk 164
[12:33:44] restarted controller (pid 9303)
[12:33:47] kill -9 at sim 2027-01-09T15:15:00+05:30 (sim minute 8115); store had proposals {'applied': 18, 'pending': 2, 'rejected': 2, 'stale': 1}, last_chunk 271
[12:33:47] restarted controller (pid 9349)
[12:33:53] controller exited with code 0
```

## Invariants

| check | result | detail |
|---|---|---|
| every simulator approval is in our store | PASS | 20 approvals, missing [] |
| no proposal sent twice | PASS | 0 duplicates |
| no approval left approved-but-unapplied | PASS | stuck []; correctly stale after a supervisor edit: ['apr_00007'] |
| no approval lapsed | PASS | [] |
| each approval applied at most once | PASS | 16 applies |
| exactly-once ingestion (calls in store == call events in chunks) | PASS | store 245008 vs simulator 245008 |
| scenario finished | PASS | 2027-01-14T00:00:00+05:30 |
| no harmful changes | PASS | {"neutral": 14, "beneficial": 10} |

Outcome with the crashes: conclusion rate 0.420 (+27.0% vs baseline), 24 changes applied {"neutral": 14, "beneficial": 10}.

**Overall: PASS**

# Crash-recovery test — public_02_incidents

`python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml --kills "D2 10:30,D3 13:00,D5 15:15"`

The controller is killed with SIGKILL (no cleanup) the moment the simulator reaches each kill time, then restarted with the same `./run.sh`.

## Log

```
[12:41:11] started controller (pid 15611) against scenarios/public_02_incidents.yaml
[12:41:12] controller died at an injected fault point (sim 2027-01-04T00:00:00+05:30); restarting
[12:41:13] controller died at an injected fault point (sim 2027-01-04T00:00:00+05:30); restarting
[12:41:13] controller died at an injected fault point (sim 2027-01-04T01:00:00+05:30); restarting
[12:41:13] controller died at an injected fault point (sim 2027-01-04T10:00:00+05:30); restarting
[12:41:14] controller died at an injected fault point (sim 2027-01-04T10:00:00+05:30); restarting
[12:41:14] controller died at an injected fault point (sim 2027-01-04T12:45:00+05:30); restarting
[12:41:14] controller died at an injected fault point (sim 2027-01-04T12:45:00+05:30); restarting
[12:41:17] kill -9 at sim 2027-01-06T10:30:00+05:30 (sim minute 3510); store had proposals {'applied': 9, 'rejected': 1}, last_chunk 106
[12:41:17] restarted controller (pid 15721)
[12:41:18] kill -9 at sim 2027-01-07T13:00:00+05:30 (sim minute 5100); store had proposals {'applied': 12, 'pending': 1, 'rejected': 1, 'stale': 1}, last_chunk 164
[12:41:18] restarted controller (pid 15743)
[12:41:22] kill -9 at sim 2027-01-09T15:15:00+05:30 (sim minute 8115); store had proposals {'applied': 17, 'pending': 1, 'rejected': 2, 'stale': 1}, last_chunk 272
[12:41:22] restarted controller (pid 15786)
[12:41:28] controller exited with code 0
```

## Invariants

| check | result | detail |
|---|---|---|
| every simulator approval is in our store | PASS | 18 approvals, missing [] |
| no proposal sent twice | PASS | 0 duplicates |
| no approval left approved-but-unapplied | PASS | stuck []; correctly stale after a supervisor edit: ['apr_00007'] |
| no approval lapsed | PASS | [] |
| each approval applied at most once | PASS | 15 applies |
| exactly-once ingestion (calls in store == call events in chunks) | PASS | store 245008 vs simulator 245008 |
| scenario finished | PASS | 2027-01-14T00:00:00+05:30 |
| no harmful changes | PASS | {"neutral": 13, "beneficial": 10} |

Outcome with the crashes: conclusion rate 0.422 (+27.6% vs baseline), 23 changes applied {"neutral": 13, "beneficial": 10}.

**Overall: PASS**

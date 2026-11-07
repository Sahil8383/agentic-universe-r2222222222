# Load test — public_03_load

`python scripts/load_test.py --scenario scenarios/public_03_load.yaml` on Darwin arm64, 10 CPUs; simulator and controller on the same machine.

- controller exit code: 0; finished: True
- simulated days: 5; wall-clock for the whole run (sim + controller): 167 s → **33 s per simulated day** (SRE's bar: < 600 s)
- history backfill: 5,537,531 events ingested in 46.5 s (**119,211 events/s**)
- live: 4,200,196 events over 417 cycles; ingest 44.4 s (**94,661 events/s**), controller time per cycle incl. decisions 0.20 s avg, 1.15 s worst
- decision lag (newest event ingested → decision): max 6 sim minutes, avg 1.6 (events are at most one tick old when we decide)
- controller peak RSS: **314 MB**; SQLite store on disk: 398 MB
- simulator start-up (world + history generation): 37 s

| sim day | events | controller seconds |
|---|---|---|
| D0 | 840,056 | 15.0 |
| D1 | 840,032 | 16.0 |
| D2 | 840,040 | 16.5 |
| D3 | 840,038 | 17.2 |
| D4 | 840,030 | 16.9 |
| D5 | 0 | 0.0 |

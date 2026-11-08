# Outcome-First Agent Loop — Submission

**Name:** Sahil Bhatt · **Date:** 2026-11-09 · **Hours actually spent (be honest):** ~10 h of my own time over 4 days (design, reviewing every module, runs and tuning; Claude Code did most of the typing) ·
**Language / storage:** Python 3.13, SQLite (WAL, one file). No dependencies beyond the simulator's own
(`httpx`, `numpy`, `pyyaml`).

AI tools: I used Claude Code as a pair programmer and for first drafts of the docs, as the README expects.
Every threshold, rule and trade-off below is one I can defend in the review.

---

## 0. TL;DR

- A deterministic reconcile loop. Each tick it ingests events exactly once, reconciles approvals, runs
  population-level detectors (caller ID, hallucination, winner decay), does a daily Bayesian review of welcome
  and voice, then takes **one** governed step per workflow (auto-apply within policy, or one batched approval).
  Time only advances after everything is committed, so `kill -9` at any point is safe.
- **public_01:** conclusion rate **+11.2%** vs baseline (every workflow up), pickup +7.9%, engagement +1.9%,
  **0 harmful** in 20 changes.
- **public_02:** **+27.6%**, pickup +24.2%, hallucinated calls 180 vs the baseline's 431. The bad prompt was
  fixed by a validated revert (5 credits, no LLM), and the bad caller ID was at 0% from the day after it broke.
  **0 harmful** in 23 changes.
- **public_03 (load):** **+18.6%** over 12 workflows, 0 harmful in 88 changes, **33 s of wall-clock per
  simulated day** (the bar is 600 s). The crash test (3× `kill -9` plus 7 injected crash points) finishes
  with the same result as a clean run.
- **Cut:** per-person call time, a real UI, and cross-tenant fairness (C15). **Partial:** dry runs and
  impact scores (honest but simple), the supervisor view (a CLI screen), and loop observability (a metrics
  table plus `status`, no pager).

## 1. Assumptions

1. **The workflow policy from `GET /workflows` is the contract.** When a memo disagrees with it, the policy
   wins, and the controller never hard-codes a policy value.
2. **`*_max_drop` guardrails are absolute rate points** (0.02 = two points of the rate), as the field name
   suggests. Under the other reading (2% relative), see §11 for the one place it matters.
3. **A supervisor's edit is the client's intent.** We adopt it into our desired state and never silently
   overwrite it. If it is harmful (the bad prompt), we *propose* a revert with evidence, and their approver
   decides.
4. **Stakeholder preferences stricter than policy are honoured voluntarily**, keyed by tenant id in
   `controller/preferences.yaml`: Kestrel gets ≤2 requests/day, none before 11:00, and no silent auto-apply of
   what a customer hears. A tenant missing from the file gets plain policy.
5. **Expired, lapsed and stale proposals are never resent.** The intent is rebuilt on fresh data and the
   current version, and gets a new proposal id. Product's "expired = rejection" is applied to all three.
6. **Every dial samples welcome, voice and caller ID independently** from the allocation (per the contract).
   So within a workflow, arms are comparable without adjusting for hour or lead mix. Caller IDs are still
   compared within the same hours, because hour drives pickup.
7. **History before D0 is representative of the same population.** It is used for call-time groups, the
   reference hallucination rate and caller-ID trust. It is not used for winner claims: only calls that ran on
   our configs count (Product's "only what actually happened").
8. **A validation run is reusable** for later requests on the same workflow and prompt (the simulator
   accepts it, and re-running costs credits without changing the decision).
9. **The approver decides only inside approver hours**, and the 2 h apply window starts at the decision. We
   tick every 15 minutes in approver hours, so an approval is applied within one tick.
10. **Harm is judged where we can measure it credibly:** workflow-wide arms, plus segments with ≥15% of
    connected calls. Smaller slices are not claimed either way.
11. **In the simulator the clock waits for us; in production it won't.** So we report decision lag and keep
    every cycle far below one tick (0.2 s average, 1.15 s worst on the load scenario).

## 2. Scope decisions

| # | Capability | Status | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Ingestion at load scale | **Built** | Everything else is only as good as this. Exactly-once: each chunk's effects and its checkpoint are one SQLite transaction | Partition by workflow for 10× (§8) |
| C2 | Joins + one cohort object | **Built** | Leads joined to calls in O(1) through a compact array indexed by lead number. `cohorts.py` owns the only cohort definition; config, call log and `view` all use its ids | Detectors are workflow- and caller-ID-level on purpose; cohort-level detection would reuse the same object |
| C3 | Bootstrap from history | **Built** | Free information: call-time groups, which caller IDs are trusted, each prompt's normal flag rate | — |
| C4 | Call time | **Partial**: per cohort built, per person cut | Per-cohort hour weights come from a chi-square test on history (≤3 groups). Per person: most leads are new each day, a returning person has few calls, and the 6×/0.25× preferred-hour multiplier is a big bet on little data | Per-person: shrink each person's hour profile toward their cohort's, and only for people with ≥N past connects |
| C5 | Caller-ID health | **Built** | A bad number is the cheapest pickup loss to fix, and safe to auto-apply where policy allows | Carrier/spam-registry integration; bringing a number back needs a human |
| C6 | Welcome/voice experiments | **Built** (main effects) | Factorial by construction (independent sampling); calibrated with an A/A test | Interaction model (the `X|welcome|voice` counters are collected but not used yet) |
| C7 | Promotion, decay, rollback | **Built**, one gap | Holdout after promotion, rollback after 2 weak checks. Never fired in public scenarios; see §13 for a gap | Keep the holdout from being dropped (§13 #2) |
| C8 | Hallucination incident | **Built**; LLM branch unexercised | Detect → revert to the known-good prompt with a validation run → approval with evidence → verify. The LLM branch is coded but no public scenario needed it | An end-to-end test with no known-good prompt |
| C9 | Governance client | **Built** | The part that breaks trust if wrong: write-ahead intent, deterministic ids, reconcile every tick, timing, fatigue | — |
| C10 | Impact, affected leads, dry run | **Partial** | Honest, not clever: replay recent arm rates under the proposed shares; options without data are labelled "exploration, no estimate" | Off-policy estimate per cohort, impact calibrated against outcomes |
| C11 | Supervisor view | **Partial** | `python -m controller view <wf>`: one text screen (live config, what's tested, what's winning, open requests, last-24h KPIs) | A small web page on the same query |
| C12 | Audit store | **Built** | Required, and the review is run from it. `why <wf> "D3 14:00"` | — |
| C13 | Crash recovery | **Built + demonstrated** | SRE's non-negotiable | — |
| C14 | Loop observability | **Partial** | A `metrics` row per cycle (events, ingest s, lag, decisions, credits, RSS) plus `status`. Alerts are `alert` rows, not pages | Export to their monitoring, plus the paging rules in §10 |
| C15 | Fair credits/attention across tenants | **Cut** | Normal operation spends 0 credits; per-workflow request caps already protect each approver | A global credit ledger with per-tenant shares |

**How I chose:** first the things that make every other number trustworthy or safe (C1, C9, C12, C13). Then
the levers with the biggest, safest outcome per hour: caller ID and call time move pickup and can't hurt
engagement; the bad-prompt fix moves engagement and hallucinations. Then experiments, done properly
(calibrated, with holdout), rather than many features done thinly. Anything that only looks good in a demo
(per-person timing, a UI) came last.

## 3. Stakeholders

Policies as `GET /workflows` returns them (identical in public_01 and public_02):

| | `nw_renewal` | `nw_kyc` | `ks_cancel` |
|---|---|---|---|
| primary / guardrail | engagement / pickup −0.02 | **pickup** / engagement −0.02 | engagement / pickup −0.02 |
| auto-apply | schedule, caller ID; max shift 0.5 | **disabled** | schedule, caller ID, welcome, voice; **max shift 0.2** |
| approver hours / latency | 10–19 / 30–240 min | 10–18 / 60–480 min | 10–19 / 20–120 min |
| TTL / max requests per 24 h | 24 h / 3 | 24 h / 2 | **12 h** / 4 |
| prompts allowed | P1, P3, P4, P5 | P1, P3 | P1–P5 |

### 3.1 Sales (Nikhil Rao)

- **Accepted:** "don't look hesitant", implemented as fewer, batched requests: at most one approval per
  workflow per day, carrying everything. Peak was 1–2 requests per workflow per 24 h. Also accepted: one number
  per client, as the **business outcome** (leads concluded ÷ uploaded), with pickup and engagement shown
  beside it.
- **Declined:**
  - a blended score (a hard constraint and Product's rule);
  - "winning by lunch, live by evening": at lunch-time sample sizes, P ≥ 0.95 picks a false winner 9.5% of
    the time even over days (§5), and winners decay;
  - rolling out the "persuasive" prompt everywhere: nothing in these tenants shows it is safe, prompt
    changes need validation and approval, it isn't even allowed on `nw_kyc`, and Product says winners don't
    cross clients.
- **Claims that are wrong:**
  - "Kestrel turned on auto-apply for everything": false. Only schedule, caller ID, welcome and voice, in
    shifts of ≤0.2; prompts and cohorts need approval.
  - "Northwind is fine with auto-apply on KYC": false. Auto-apply is disabled on `nw_kyc`, and Northwind's
    Compliance head requires a named human approval for anything customer-facing. A QBR remark doesn't
    override a signed policy.
- **Reply:**
  > Nikhil, you'll have one number per client for the summit: leads concluded per lead uploaded, which is the
  > outcome the client pays for. In the simulator that's +11% to +28% vs doing nothing, with zero harmful
  > changes. Next to it we show pickup and engagement separately, because Product and both clients require
  > that. Two corrections before anyone says them on stage: Kestrel's auto-apply covers timing, caller ID,
  > welcome and voice in small steps, not prompts. And KYC has auto-apply switched off in Northwind's policy.
  > "Winning by lunch" is mostly noise; we promote when the evidence holds, usually within days. I'll gladly
  > help build the slide.

### 3.2 Northwind Compliance (Meera Iyer)

- **Accepted, all of it:**
  - Named human approval for anything customer-facing: Northwind's policy already allows no auto-apply of
    welcome, voice or prompt.
  - Validation evidence on every script change: the controller refuses to send a prompt change without a
    `validation_run_id`.
  - "Slow and right": calibrated bars, a holdout, small exploration shares.
  - Requests only inside approver hours: `submit_window_ok` also refuses any request that might expire
    before its slowest possible decision.
- **The IRDAI question** is answerable now, per person and per call (query and real output in §10). It
  gives the variant, the config version, who approved it and the rationale that was live.
- **Claim that's half wrong:** "call timing and caller-ID rotation are covered by the policy we signed" is
  true for renewal, **false for KYC**, where auto-apply is disabled. The controller reads the policy, so on
  KYC those changes went through approval: in public_02, `nw_kyc` v3 and v4 (caller ID) were approved
  requests.
- **Pushed back:** only on the format of the weekly summary. The audit store has every auto-applied change
  with its reason; the formatted weekly email isn't built.
- **Reply:**
  > Meera, agreed on all five. Nothing a customer hears changes on Northwind without your approver's named
  > approval, and every script change carries a validation run id. One correction: timing and caller-ID
  > rotation are covered by policy for renewal only. KYC has auto-apply disabled, so on KYC those changes
  > also come to your approvers. For IRDAI: our store records every call with its exact variant and config
  > version, and links each version to the approval and the evidence behind it. We can answer "what did this
  > customer hear on this day, and why" in minutes, not 24 hours. The weekly summary of policy-covered
  > changes comes from the same store.

### 3.3 Platform & SRE (Kabir Menon)

- **Accepted, all of it:**
  - The kill/restart test (§9): 3× `kill -9` plus 7 injected crashes at the riskiest lines. All invariants
    pass and the final result is identical to a clean run.
  - Idempotency: write-ahead intent, deterministic proposal ids, reconcile each tick.
  - No new stateful infrastructure: one SQLite file, and the schema maps one-to-one onto Postgres.
  - One simulated day of public_03 in 33 s (bar: 600 s).
  - Loop health without logs: `python -m controller status`.
- **Not done:** wiring alerts to their pager. Today they are `alert` rows in the store, surfaced by
  `status`. §10 lists what should page and what shouldn't.
- **Reply:**
  > Kabir, the non-negotiables are covered, with evidence. reports/crash_recovery.md kills the controller
  > three times with kill -9 plus seven deliberate crashes at the riskiest lines. Nothing is applied twice or
  > lost, and the run ends identical to a clean one. On public_03: 33 s per simulated day, 95k events/s,
  > 314 MB. State is one SQLite file that maps one-to-one onto the Postgres you already run, so nothing new
  > to operate at 2 a.m. At 10× the first break is single-core JSON parsing; the fix is sharding by workflow
  > (notes in SUBMISSION §8). `controller status` shows lag, cycle time and credits. Paging rules are written
  > but not wired to your pager yet.

### 3.4 Kestrel Ops supervisor (Anita Desai)

- **Accepted:**
  - Never undo her changes behind her back. Her edit becomes our new intent, and the experiment it
    interrupted is paused. In public_02 she shipped a prompt that tripled hallucination flags; we sent *her
    approver* a revert request with the evidence, and it was approved.
  - **Two requests a day and none before 11:00, voluntarily.** The policy allows 4 a day from 10:00. One of
    the two slots is kept for incidents, and an incident fix may go out before 11:00.
  - One request with five things in it: batching is the default.
  - No silent changes to what customers hear: policy would let us auto-apply welcome and voice on Kestrel,
    and we don't.
  - One screen: `controller view ks_cancel`.
- **Pushed back:** "tell me before *anything* changes." Caller-ID and timing changes still auto-apply,
  because waiting for a standup while a spam-flagged number keeps dialling costs her pickups. They show up in
  her screen and the log the moment they happen.
- **Her compliance-script story** is exactly why validation (a hallucination measure) isn't treated as an
  engagement measure. Our first fix is always a revert to the prompt her customers already stayed on, not
  an untested "safer" prompt.
- **Reply:**
  > Anita, done: at most two requests a day, none before 11:00, and each one bundles everything. One slot is
  > kept for a genuine incident. We never undo your edits silently. If one looks harmful, you get a request
  > with the numbers and you decide. Welcome, voice and prompt never change without your approval, even
  > though the policy would allow some of it. Caller ID and call timing do change automatically when a
  > number goes bad, because waiting costs you pickups. You see them on your screen the moment they happen.
  > `controller view` is that one screen: what's tested, what's winning, what we need from you, what changed
  > today.

### 3.5 Product (Tara Kulkarni)

- **Accepted, every definition:**
  - pickup and engagement are never blended;
  - one cohort object (`cohorts.py`; its ids appear in the config, in every call row and in the view);
  - every proposal carries a diff, affected leads, a dry run, an impact score and a rationale;
  - expired, lapsed and stale proposals are rebuilt fresh, never resent;
  - learning uses only the variant each call actually ran with;
  - a winner changes only its own workflow.
- **The open question, answered with its cost:**
  - **Minimum cohort size:** a segment needs ≥15% of a workflow's connected calls to become a cohort, and
    each arm needs ≥300 connected calls before any winner claim. 300 detects about a +20% relative lift at
    80% power; smaller effects take longer.
  - **Evaluation window:** the last 4 days, each day weighted half as much as the next (effects decay).
  - **Bar:** P(better) ≥ 0.975 workflow-wide and ≥ 0.99 per segment. The A/A table in §5 shows the cost:
    2.8% false winners, but a real +10% lift is found less than half the time within a 10-day scenario.
- **Pushed back:** three P0s. Caller-ID health is built. The supervisor view is minimal. Per-person call time
  is deferred: few returning people with few calls each, and a 6× multiplier is a big bet on thin data.
- **Reply:**
  > Tara, all definitions are held as written, including expired = rejection, rebuilt with fresh data. Your
  > open question: a segment needs ≥15% of the workflow's connected calls and each arm ≥300 connected calls;
  > decisions use the last 4 days with recent days weighted more; the bar is 97.5% (99% per segment). Cost:
  > an A/A test puts false winners at 2.8%, and a real +10% is found less than half the time within ten
  > days. That's the trade I'd make for regulated clients. Of your three P0s, caller-ID health is done, the
  > supervisor view is minimal, and per-person timing waits. The data per person is too thin to justify a 6×
  > hour bet yet.

### 3.6 CTO (Aryamann)

- **Accepted:** this memo is the rubric. Outcomes a compliance head can sign; no compute without a decision
  behind it (normal operation spends 0 credits, and the only incident cost 5); design for the envelope; choose
  and say no; docs for the person who gets paged.
- **The claims that turned out to be untrue:**
  1. Sales: Kestrel auto-applies everything.
  2. Sales: Northwind is fine with auto-apply on KYC.
  3. Compliance: timing and caller ID are covered by policy, which holds for renewal but not KYC.
  4. Anita's "two a day, not before 11" isn't the contract (4 a day, from 10:00). It's a preference we chose
     to honour.
  5. Product's per-person timing as the summit headline: it is the least supported by the data.
- **Reply:**
  > Aryamann, I chose depth on the parts that make every other number trustworthy (exactly-once ingest, the
  > approval state machine, the audit store, crash safety), then the safest big levers: caller ID, call time,
  > the prompt incident, and calibrated welcome/voice experiments. I said no to per-person call time, a UI
  > and cross-tenant fairness. The messages are in §3. Five memo claims didn't match the policies; the
  > controller follows the API, never the memos. Credits spent across both public scenarios: 5. What breaks
  > first at 10× is single-core parsing; the fix is sharding by workflow. The runbook is in §9 and §10.

## 4. Architecture

```
                 ┌────────────────────────── one cycle (every 15 min busy / 60 min idle) ──────────────────────────┐
 simulator       │                                                                                                  │
 GET /events ───▶│ ingest.py   chunk → counters (marginal keys) + call log + checkpoint   [ONE transaction]         │
                 │                                                                                                  │
 GET /approvals ▶│ governance.reconcile   submitting / pending / approved / applying → applied | stale | lapsed …   │
 GET /config  ──▶│ loop._refresh_config   new versions; a supervisor edit → adopt as intent, pause experiments     │
                 │                                                                                                  │
                 │ DESIRED STATE (decide what we want; writes only to SQLite `state`)                               │
                 │   detectors.py   caller-ID health (hourly), hallucination spike (every tick), decay (4-hourly)   │
                 │   incident flow  validate known-good prompt → propose → verify                                   │
                 │   experiments.py daily review of welcome/voice; segment winners → cohorts.py plan               │
                 │   schedule.py    call-time groups (once, from history)                                           │
                 │                                                                                                  │
                 │ ACTUAL STATE (make reality match: one step per workflow)                                         │
                 │   planner.desired_config vs live config → policy.changed_dims                                   │
                 │     ├─ auto-apply (policy + preference allow it), ≤ max_shift ─────────────▶ PUT /config         │
                 │     └─ else one batched approval (budget, approver hours, TTL) ────────────▶ POST /approvals     │
                 │                                                    approved → POST /workflows/{id}/apply         │
                 │ metrics row                                                                                      │
                 └──────────────────────────────────────────────── then POST /sim/advance ─────────────────────────┘

 SQLite (state/controller.db, WAL): meta · workflows · feature_codes · leads · counters · calls · config_versions
                                    · proposals · decisions · state · validations · metrics
 CLI (read-only): python -m controller status | view <wf> | why <wf> "D3 14:00" | audit <wf>
```

- **Where state lives:** all of it is in SQLite. Memory holds only a cache of the counters and the lead →
  features array, both rebuilt from SQLite at start.
- **Level-triggered:** detectors and reviews only edit the desired state. The planner diffs desired against
  live every tick. A crash anywhere just means the next tick sees the same gap.
- **On restart:** the same `__init__` and the same `cycle()`. There is no recovery mode.
  - The ingest checkpoint resumes after the last committed chunk.
  - `governance.reconcile` resolves any proposal left in `submitting` or `applying` against
    `/approvals` and `/config/history`.
  - Time hasn't moved, because we advance only after a cycle commits.
- **New world:** if the simulator was restarted or the scenario differs, the old DB is archived rather than
  mixed in (`_fresh_db_if_new_world`).
- **Rule:** the controller never imports `sim`, never reads `runs/` and never parses `scenarios/`. HTTP only.

## 5. The learning loop

**Cohorts.**
- Two sources, combined by one object (`cohorts.py`):
  1. **call-time groups** from history: a chi-square homogeneity test across each feature's hour-of-day
     pickup curves; the feature with z > 3 wins, and its values are merged into ≤3 groups by similar shape;
  2. **experiment segments**: one feature value where another option beats the workflow choice at P ≥ 0.99
     with ≥15% of connected calls.
- The config is their cross-product in first-match order, capped at 20 cohorts, so traffic is never split
  into many tiny cells. Statistics run on **marginal** counters (`AS|lang=ta|voice=V2`), not on the
  cross-product, so a cohort never needs its own sample to be estimated.

**Experiment design.**
- Allocation:
  - untested options get 5% each, ≤15% per dimension, so a −15% arm costs ≤0.75% of engagement;
  - a challenger's share moves with P(better) only outside a hold band (0.35–0.80), at most 10 points per
    day, capped at 30%, and only if it is still winning on the last 2 days;
  - the incumbent gets the rest.
- Exploration vs exploitation is therefore a **capped, damped Thompson-like rule**, not pure Thompson
  sampling. Pure Thompson swung V4 from 24% to 5% to 27% on noise in my early runs, and a "harmful change"
  costs the client trust.
- **Two dimensions:** welcome and voice are sampled independently on every dial, so one config is a
  factorial experiment and both main effects are read from the same calls. Interactions are not modelled:
  the welcome × voice counters are collected but unused (§13).

**Deciding a winner.**
- Beta posteriors on time-decayed counts, seeded, so every decision is deterministic and replayable. A
  challenger is promoted when **all** of these hold:
  - P(challenger > incumbent) ≥ 0.975;
  - expected loss < 0.003;
  - ≥300 connected calls per arm;
  - the pickup guardrail holds;
  - it is still ahead on the last 2 days.
- Losers at P(worse) ≥ 0.95 go to 0.
- **Multiple comparisons and peeking** are handled by calibration rather than a formula: an A/A test with
  two identical arms, the rule applied every day for 8 days, at real traffic levels (60 vs 900 calls/day).
  It is a unit test (`tests/test_controller_units.py::test_aa_false_promotion_rate_is_low`):

  | Bar | False-winner rate | Power at +10% | Power at +15% |
  |---|---|---|---|
  | P ≥ 0.95 | 9.5% | 0.59 | 0.88 |
  | **P ≥ 0.975 (chosen)** | **2.8%** | 0.43 | 0.71 |
  | P ≥ 0.99 | 0.7% | 0.23 | 0.55 |

  Segments use 0.99 because there are many more of them.

**Decay and rollback.** After a promotion the old incumbent keeps 10% as a holdout. Every 4 hours we compare
winner vs holdout on the last 2 days. If P(winner still better) < 0.5 on two checks in a row, we roll back:
the winner goes to 0 and its share goes to the holdout (an urgent change). Speed: at least two 4-hourly
checks once the 2-day window turns, so likely within a day. It never fired in the public scenarios, so that's
an estimate, not a measurement. Separately, counts are time-decayed (half-life 1 day), so a fading challenger loses
share in the daily review before it can be promoted. That is what kept nw_renewal's decaying W3 from ever
being promoted.

**Call time and caller ID.**
- Call time: per cohort, from history (above). Hour weights are clipped to [0.4, 2.5], so we tilt but never
  switch an hour off (an empty hour wastes capacity), and shrunk toward the group's mean with a pseudo-count
  of 200. Per person: not built (§2).
- Caller ID:
  - each number is compared with the *other established numbers in the same hours*;
  - flagged when its pickup is <70% of expected over ≥200 dials, with a one-sided binomial p < 1e-4;
  - traffic is spread evenly over healthy numbers, because load wears a number out;
  - a never-used number starts on **probation** at 5% (≤10% in total) and is judged on everything since its
    introduction; it graduates at ≥90% of its peers over 250 dials;
  - flagged numbers are never brought back automatically.

**Guardrail KPI.** For engagement-primary workflows, promotion requires the challenger's pickup not to fall by
more than the guardrail (welcome and voice can't move pickup much, but we check). For `nw_kyc`
(pickup-primary), the levers we pull for pickup (caller ID, timing) don't touch engagement, but the
engagement guardrail is **not actively monitored**. That's the weakest point of the loop (§11, §13).

## 6. Governance

```
 submitting ──200──▶ pending ──approved──▶ applying ──200──▶ applied
     │                  ├─rejected──▶ rejected        ├─410──▶ lapsed
     │                  └─expired───▶ expired         └─409 stale──▶ stale
     ├─409 stale_base_version──▶ stale     (never reached the approver; costs no slot)
     ├─409 duplicate_proposal_id──▶ adopt it from GET /approvals (we sent it before a crash)
     ├─5xx / no answer──▶ stays submitting; the next tick resends the SAME id (safe)
     └─422──▶ failed (our bug: alert)
 auto path:  submitting ──PUT 200──▶ applied | 403 refused | 409 stale | 409 duplicate → adopt from /config/history
```

**Re-proposal.** We never resend a stale, expired, lapsed or rejected request. The desired state is still in
our store, so the planner sees the gap again and builds a new request on the current version. The id is
`{wf}:{kind}:v{base}:{config hash}:{n}`, so it differs because the base version differs. A judgement
rejection is retried after 18 h with fresh data. Real example (public_02, ks_cancel):

```
D3 11:30 submitted apr_00007 (voice V1 75%→65%, V2 5%→15%) on base v8
D3 11:45 supervisor changed prompt_version → v9
D3 12:15 apr_00007 approved → apply → 409 stale: "approved but config moved on (now v9); will re-propose on top"
D4 13:30 same voice change re-proposed on v10 → approved → applied
```

**Batching and timing.**
- At most **one open proposal per workflow**, and it carries every pending dimension. No auto-apply while one
  is pending: our own change would make it stale.
- Budget: `min(policy max, client preference) − 1` slot kept for incidents.
- A request goes out only if:
  - the approver is at work;
  - it is after the client's preferred hour (non-urgent only);
  - the slowest possible decision (latency, pushed into approver hours) lands before the TTL. This matters
    on Kestrel's 12 h TTL, where anything sent after ~17:00 would expire overnight.
- Routine changes go once a day, after the morning review. In public_02 the peak was 2 requests/24 h on any
  workflow, with 0 expired and 0 lapsed. The naive example controller expired 7 of its 14 requests on
  public_01.
- **Urgent requests carry only the urgent dims**, so a safety fix isn't held hostage by a routine voice
  tweak.

**When we auto-apply, and when we don't.** Only what the policy allows, in steps of ≤ `max_shift`, with one
step per tick: caller ID, schedule, and on Kestrel also welcome and voice. We **voluntarily** don't
auto-apply anything a customer hears on Kestrel (Anita's ask). Northwind's policy already forbids it.

**Proposal content (C10), and how honest it is.**
- **Diff:** from/to per dimension and per cohort.
- **Dry run:** for welcome and voice, each option's engaged/connected over the last 4 days, re-weighted by
  current vs proposed shares. Options with <50 connected calls are listed as `no_support` ("exploration: no
  estimate claimed"). For caller ID it attaches lifetime dials and recent health; for schedule, the
  history evidence; for a prompt, the validation run and the incident detector output.
- **Affected leads:** active leads (uploaded − engaged over the cadence length) plus the next 24 h of
  uploads. Every change touches the default cohort, so in practice this is "everyone". That's
  conservative.
- **Impact score:** a floor of 0.05; otherwise the dry-run lift ÷ 10%, capped at 1; urgent items carry their
  own (0.6 decay, 0.7 caller ID, 0.9 prompt incident). It ranks consistently, but it isn't calibrated
  against realised outcomes (§13).

**Supervisor edits mid-experiment.**
- The edit is adopted as the new intent for every dimension it touched.
- An experiment on that dimension is **paused** and marked "interrupted at vN"; the edit is logged as a
  decision.
- Caller-ID rebalancing is held for 24 h (we only remove flagged numbers).
- Any of our approved-but-unapplied requests become stale and are rebuilt on top of the edit.

Real example: public_02 D5 15:15, "supervisor changed voice (v7); voice experiment paused: interrupted by
supervisor at v7".

## 7. Incidents

**Detection.**
- **Hallucination:** counts connected calls on the *current* prompt since it went live (capped at the last
  4 h). Reference = the flag rate of the prompt that ran longest before it, smoothed. Fires on a one-sided
  binomial test with p < 0.001 and n ≥ 150. Expected false-positive rate: windows overlap tick to tick, so
  the 0.001 per check is not independent. Treated as independent (the worst case), that's about 0.5 false
  alarms per workflow per 10-day scenario; in reality fewer. A false alarm costs 5 credits and one approval
  request that reverts to an equally good prompt. None fired in the public runs.
- **Caller ID:** p < 1e-4 per check, about 1,000 checks per scenario, so ~0.1 expected false flags per
  scenario.

**Picking a fix.**
1. **Revert to the known-good prompt** (the reference). Validate it once (n=500, 5 credits, pass-rate SE
   ≈ 0.5 pt) and propose it with the validation id.
2. Only if there is no known-good prompt, or the revert fails verification: `POST /llm/suggest-fixes`
   (3 credits), validate each candidate, propose the best that passes. If none passes, the incident goes to
   "stuck" and pages a human.
3. The LLM proposes; it never decides. The validation number and a fixed rule do.

**Credits:** 5 in total across both public scenarios. Experiments and caller-ID work spend nothing.

**Verify after apply:** after the fix is live, re-run the detector on calls since the apply. Close when
n ≥ 150 and p > 0.01; otherwise escalate to the LLM path.

**Timeline, public_02, ks_cancel** (from `controller audit ks_cancel`):

```
D3 11:30–11:45  supervisor ships P2 (v9)                       → adopted as intent, detector re-baselined to P2
D3 13:30        incident_open: P2 flags 12/175 = 6.9% vs reference 1.5% (p = 1.4e-05)
D3 13:30        validated P1: pass rate 0.986 on n=500 (5 credits)
D3 13:30        proposed P2→P1 as apr_00008 (urgent: prompt only; Kestrel's 11:00 rule doesn't block incidents)
D3 15:30        approved and applied as v10
D3 17:15        verified: 1/156 flagged since the fix → closed
```

The grader's MTTR is 240 min. 120 of those were approver latency; most of the rest was the wait for 150
connected calls under P2. Hallucinated calls on ks_cancel: 92 vs the baseline's 348.

## 8. Scale

Measured with `python scripts/load_test.py` on public_03_load (`reports/load_public_03.md`). This was a
laptop, with the simulator on the same machine:

| Measure | Value |
|---|---|
| Wall-clock per simulated day | **33 s** (bar: < 600 s) |
| History backfill | 5.54M events in 46.5 s (**119k events/s**) |
| Live ingest | 4.2M events, **95k events/s** |
| Controller time per cycle (ingest + decisions) | 0.20 s average, 1.15 s worst |
| Decision lag (newest event ingested → decision) | ≤ 6 sim-minutes, 1.6 average |
| Peak RSS | **314 MB**; SQLite on disk ≈ 400 MB |

**Why it's fast:**
- counters are marginal keys, not a cross-product, so one call bumps about 20 keys;
- lead features are a `uint16` array indexed by the lead's number;
- one transaction per chunk;
- detectors read aggregates;
- the only per-call SQL is the hourly caller-ID check over 3 h of calls.

**What breaks first at 10× (~8.5M events/day, ~1M dials in peak hours):**
1. **Single-core JSON parsing** (~100k events/s per core). Fix: partition by workflow. Events, counters,
   state and decisions are already per workflow, so N workers each own a hash range of workflows. The only
   shared things are the credit ledger and per-tenant preferences.
2. **SQLite's single writer and call-log growth.** Fix: their existing Postgres, with the call log
   partitioned by day and rolled up into counters after N days (the audit question needs the per-call rows
   for the retention period, not forever).
3. **Caller-ID checks scanning raw calls.** Fix: read them from 15-minute counter buckets instead.

**Partitioning across tenants:** shard key = workflow id, with tenant affinity, so one tenant's preferences
and approver budget live on one worker. Add a global credit ledger (the C15 work) once credits matter.

## 9. Reliability

**Crash-recovery test:** `python scripts/crash_test.py` (`reports/crash_recovery.md`).
- **Method:** run public_02 and inject a crash (`os._exit`, no cleanup) the first time the process reaches
  each of 7 points, so we hit windows a random kill rarely lands in:
  - after the ingest work but before its commit;
  - after writing a proposal intent but before sending it;
  - after the simulator accepted it but before we recorded the answer;
  - before and after an apply;
  - before and after an auto-apply.
  Then `kill -9` at D2 10:30, D3 13:00 and D5 15:15. Every restart is the same `./run.sh`.
- **Invariants checked:**
  - every simulator approval is in our store;
  - no proposal was sent twice;
  - nothing was left approved-but-unapplied, except one correctly marked stale;
  - nothing lapsed;
  - every approval was applied at most once;
  - **exactly-once ingest**: 245,008 calls in our store = 245,008 call events in the chunks;
  - the scenario finished;
  - 0 harmful changes.

  All passed, and the final outcome was identical to the clean run (+27.6%).
- **State lost:** none that was committed. The work of the cycle in progress is redone; the clock hadn't
  moved, so it is redone at the same sim minute.

**Idempotency keys:**
- Proposal ids are deterministic (`{wf}:{kind}:v{base}:{config hash}:{n}`). The intent row is committed
  before the HTTP call, so a resend gets `409 duplicate` and we adopt the existing approval.
- Ingest uses chunk number + checkpoint in the same transaction, plus `INSERT OR IGNORE` on `event_id`.
- `POST /sim/advance` is never blindly retried: after a lost response we read `/sim/state`.

**4xx / 5xx behaviour:**

| Response | Behaviour |
|---|---|
| Connection refused | Retry with backoff (the request never arrived) |
| 5xx / timeout on a read | Retry with backoff, up to 8 times |
| 5xx / timeout on a write | Unknown outcome: the row stays `submitting` and the next tick resends the same id |
| 409 `stale_base_version` | Mark stale; rebuild on the current version |
| 409 `duplicate_proposal_id` | Adopt the existing approval / config version |
| 410 lapsed | Mark lapsed, record a decision; re-propose with fresh data |
| 402 out of credits | Incident step waits and retries next cycle; alert row |
| 403 / 422 | Our payload is wrong: mark failed, write an `alert` (a bug, not a retry) |

## 10. Audit & observability

**"Why did ks_cancel change at D3 16:00, who approved it, and what happened after?"** This is a real command
on the saved public_02 store:

```
$ python -m controller why ks_cancel "D3 16:00"
ks_cancel at D3 16:00 was running config v10, applied D3 15:30 by controller_approved (changed: prompt_version)
  proposal ks_cancel:incident:v9:2245953a8dc4fc11:0 (incident, via approval), sent D3 13:30, final state applied
  summary:   prompt P2→P1
  rationale: since P2 went live, 12/175 connected calls were flagged (6.9%) vs 1.5% before (p=1.4e-05); revert to the last known-good prompt: P1, validation val_00001 passed 98.6%
  impact 0.9, affected leads 4537
  approved as apr_00008 at D3 15:30 by the client's approver; applied D3 15:30
  trigger D3 11:00 [review] daily review: welcome_message {'W1': 0.9, 'W2': 0.05, 'W3': 0.05}; voice {'V1': 0.65, 'V2': 0.15, 'V3': 0.05, 'V4': 0.15}
  trigger D3 11:45 [supervisor_edit] supervisor changed prompt_version (v9); adopted as the new intent
  trigger D3 13:30 [incident_open] hallucination flags on P2: 12/175 = 6.9% vs reference 1.5% (p=1.4e-05)
  trigger D3 13:30 [incident] proposing P1 (revert to the last known-good prompt)
  trigger D3 15:30 [incident] fix P1 is live; verifying the flag rate
  24h before: {'dials': 4800, 'pickup': 0.254, 'engagement': 0.4914, 'halluc_flag_rate': 0.0254}
  24h after:  {'dials': 4800, 'pickup': 0.2548, 'engagement': 0.5012, 'halluc_flag_rate': 0.0147}
  next change: v11 by controller_approved at D4 14:30
```

**Compliance's IRDAI question:** "what did this customer hear, and why was it live?"

```sql
SELECT printf('D%d %02d:%02d', c.minute/1440, (c.minute%1440)/60, c.minute%60) AS at, c.person,
       'v'||c.version AS config, c.cohort, c.wm, c.voice, c.did, c.prompt,
       CASE c.outcome & 3 WHEN 3 THEN 'engaged' WHEN 1 THEN 'connected' ELSE 'no answer' END AS outcome,
       v.actor, p.approval_id, substr(json_extract(p.body_json,'$.rationale'),1,90) AS why_live
FROM calls c JOIN config_versions v ON v.wid = c.wid AND v.version = c.version
LEFT JOIN proposals p ON p.proposal_id = v.proposal_id
WHERE c.wid = 'ks_cancel' AND c.person = 'ks_cancel:p0018374' ORDER BY c.minute;
```
```
at        person              config  cohort  wm  voice  did  prompt  outcome    actor                approval_id  why_live
D3 16:12  ks_cancel:p0018374  v10     g1      W1  V1     D3   P1      connected  controller_approved  apr_00008    since P2 went live, 12/175 connected calls were flagged (6.9%) vs 1.5% before (p=1.4e-05);
D3 19:50  ks_cancel:p0018374  v10     g1      W1  V1     D3   P1      no answer  controller_approved  apr_00008    since P2 went live, …
D4 09:03  ks_cancel:p0018374  v10     g1      W1  V1     D1   P1      engaged    controller_approved  apr_00008    since P2 went live, …
```

The other commands:
- `controller audit <wf>`: every decision in order (detect, propose, submitted, approved, applied, stale,
  review, experiment_*, incident*, supervisor_edit, alert).
- `controller view <wf>`: the supervisor screen.
- `controller status`: loop health (sim time, last cycle time, ingest lag, events/s, credits, RSS, proposals
  by state, recent alerts).

**Would page (each message says what to look at):**
- ingest lag above 2 ticks, or no new chunk while dialling (the loop is blind);
- incident "stuck": no candidate passed validation (a human must pick a prompt);
- every caller ID on a workflow unhealthy (a human must add numbers);
- a 403/422 on our own payload (our bug; stops a workflow's changes);
- an apply failing twice (a 2 h apply window is running out);
- credits too low for one more incident (validation + LLM ≈ 8).

**Would not page** (morning report): rejections, inconclusive experiments, stale proposals (self-healing),
single 5xx retries, caller-ID flags that were already auto-fixed.

Today these are `alert` / `detect` decision rows shown by `status`; wiring them to a pager is the remaining
step.

## 11. Results

**public_01_dev** (`reports/public_01_dev.md`):

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.380 | +11.4% | 0.262 | +7.1% | 0.506 | +2.9% | 24% | 68 (57) |
| nw_kyc | 0.436 | +19.0% | 0.285 | +17.9% | 0.592 | -0.9% | 3% | 31 (21) |
| nw_renewal | 0.444 | +6.6% | 0.274 | +3.9% | 0.551 | +1.8% | 3% | 44 (54) |
| ALL | 0.413 | +11.2% | 0.271 | +7.9% | 0.539 | +1.9% | 11% | 143 (132) |

20 changes applied: 12 neutral, 8 beneficial, **0 harmful**. 15 approval requests: 13 approved, 2 rejected
(judgement), 0 expired, 0 lapsed.

**public_02_incidents** (`reports/public_02_incidents.md`):

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.362 | +53.8% | 0.250 | +38.2% | 0.494 | +5.6% | 35% | 92 (348) |
| nw_kyc | 0.458 | +22.6% | 0.290 | +23.9% | 0.594 | -2.3% | -2% | 30 (29) |
| nw_renewal | 0.480 | +10.9% | 0.293 | +11.5% | 0.547 | -1.4% | 7% | 58 (54) |
| ALL | 0.422 | +27.6% | 0.273 | +24.2% | 0.534 | +0.3% | 22% | 180 (431) |

23 changes applied: 13 neutral, 10 beneficial, **0 harmful**. 18 requests: 16 approved, 2 rejected, 0
expired, 0 lapsed, 1 apply conflict (the supervisor race in §6, rebuilt and applied the next day). The
flagged caller ID had 5.5% of dials on the day it broke and 0% after. Bad prompt MTTR: 240 min. Credits
used: 5.

**Where we helped:**
- **Pickup**, everywhere: spreading calls over healthy caller IDs (load wears a number out, and `nw_kyc`
  dialled 100% from one number), removing the bad one, and per-cohort call-time weights. Pickup is
  `nw_kyc`'s primary KPI: +18% and +24%.
- **Engagement** on ks_cancel: the V4 voice promotion in public_01 (+5.6 points on the change that applied
  it), and the prompt revert in public_02.

**Where we were weak:**
- **Engagement gap captured** is low on Northwind (3–7%). Welcome/voice gains there are small and need more
  data than 8–10 days give at a 2.8% false-winner bar. That's the price of the bar.
- **nw_kyc engagement**: −0.9% (public_01) and **−2.3% relative (−1.4 points)** (public_02) vs baseline, on
  the guardrail KPI. Our engagement levers on nw_kyc are flat by the grader's own expected-engagement
  measure, and first-attempt engagement is down too. With ~5,000 connected calls per run, the standard
  error is ≈ 1.1%, so this is mostly noise, plus likely a survivorship effect: more pickups means more
  hard-to-engage people reach the agent. Under assumption 2 (absolute points) it is inside the 0.02
  guardrail; under a relative reading, public_02 is 0.3% over. **What I'd change:** an active
  guardrail monitor for pickup-primary workflows, which alerts and, if the drop is credible, holds the
  pickup changes.
- **public_03 hallucinated calls** are *higher* than baseline (5,230 vs 4,667). This is because we connect
  11% more calls at the same per-call flag rate; it isn't a quality drop. Reported as is.

**How I got here.** Six tuning rounds, each one triggered by a harmful change in the grade report and fixed
with a general rule, never a scenario constant:

| Run | public_01 Δ | Harmful | What I learned → changed |
|---|---|---|---|
| naive example | +0.6% | 0 | 7 of 14 requests expired: timing matters as much as content |
| v1 | +12.0% | **6** | All were caller-ID moves. A new number got a full share after 300 dials with no health check, a 3 h window can't judge a 5% share, and re-probing a flagged number brought it back twice → **probation** judged since introduction, graduation at ≥90% of peers, flagged numbers stay off |
| v2 | +10.0% | 2 | A voice share swung 24%→5%→27% on noise; a welcome message was raised as its novelty faded → **hold band, 10-point step cap, recent-days gate**; caller-ID p < 1e-4 |
| v3 | +9.4% | 1 | A flat window "remembers" a faded variant's good days → **time-decayed counts** |
| v4 | +11.8% | 0 (1 on public_02) | A truly −10% voice raised on ~150 lucky calls → challengers grow only at P > 0.8 |
| v5 | +10.6% | 0 / 0 | **A/A test**: P ≥ 0.95 → 9.5% false winners → bar 0.975. Urgent requests carry only urgent dims |
| **v6 (submitted)** | **+11.2%** | **0 / 0 / 0** | |

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| An LLM agent deciding changes | The brief is "agents that earn autonomy" | Constraint 4: winners are statistical and deterministic. The LLM is used once (prompt fix candidates) and never decides |
| Pure Thompson sampling | Standard for bandits; good regret | It swung shares on noise and created "harmful" applied changes. Capped, damped shares with a hold band instead |
| Frequentist sequential tests (mSPRT, alpha spending) | Exact error control with peeking | Harder to explain to a supervisor than "97.5% likely better". I calibrated the Bayesian bar with an A/A test instead, which also covers peeking |
| Full cross-product cohorts | Catches every interaction | Fragments traffic; no cell reaches 300 calls. Marginal segments, ≥15% share, ≤20 cohorts |
| Per-person call time | Product's summit headline | Thin data per person, and a 6× bet; per-cohort first |
| CUSUM / change-point decay detection | Faster decay detection | Two-strike rule on a winner-vs-holdout window is simple, explainable and good enough at this volume |
| Postgres + Redis | SRE already runs them | Nothing to operate for a take-home. SQLite's schema maps one-to-one onto Postgres for production |
| Keep raw events, aggregate at query time | Maximum flexibility | Too slow at 850k events/day. Marginal counters, plus a call log only for live days (audit) |
| Async / multi-process ingest | Throughput | 33 s per day is already 18× under the bar. Sharding by workflow is the 10× plan |
| LLM fixes first for incidents | The memo flow says "detect → LLM suggest" | A validated revert to a known-good prompt is cheaper (5 vs 8+ credits), faster, and the safest evidence for a compliance head |

## 13. Known weaknesses

1. **The engagement guardrail isn't monitored on pickup-primary workflows** (nw_kyc). Only the pickup
   guardrail is checked, at promotion time. See §11.
2. **A promoted winner can lose its holdout.** In public_01, V4 was promoted over V1 on ks_cancel (D4), and
   the next day's review dropped V1 as a clear loser. With the holdout at 0%, the decay check has nothing to
   compare against, so decay monitoring for V4 silently stopped. Fix: never drop the holdout while a
   promotion is active.
3. **Decay rollback never fired in the public scenarios**, and the LLM branch of the incident flow never
   ran. Both are coded, but only partly tested.
4. **Welcome × voice interactions aren't modelled.** Counters are collected, not used.
5. **Low power by design:** a real +10% lift is found less than half the time within a scenario. Small
   wins on Northwind go unclaimed.
6. **Impact scores aren't calibrated**, and affected leads is effectively "everyone" for any change that
   touches the default cohort.
7. **Detection latency scales with volume:** the hallucination detector needs 150 connected calls on the
   new prompt (about 2 h on ks_cancel; longer on a small workflow).
8. **Tuned while watching public grades.** Every fix is a general rule (probation, hysteresis, recency,
   calibration), but the constants were chosen with public results in view. The hidden scenarios are the
   real test.
9. **Some constants are declared but not enforced**: `INCIDENT_CREDIT_RESERVE` (it holds today only because
   nothing but incidents spends credits), `APPLY_MARGIN_MIN`, `MC_DRAWS`.
10. **Single process, single SQLite writer** (fine at 1×, the first thing to change at 10×), and the
    supervisor view is a text screen.

## 14. If this went to production at Formi next month

In order, first two weeks:

1. **Shadow mode on one real workflow.** Run the loop read-only against production events, log the
   proposals it *would* send, and compare with what humans did. Nothing is applied until the dry runs match
   reality.
2. **Close the two safety gaps:** the engagement guardrail monitor, and never dropping a holdout during a
   promotion. Add tests for both, plus an end-to-end test of the LLM incident branch.
3. **Move the store to the existing Postgres** (same schema), call log partitioned by day, with a retention
   policy agreed with Compliance (the IRDAI question needs per-call rows).
4. **Wire the paging rules in §10** into on-call, with a runbook entry per alert, and export the `metrics`
   table to their dashboards.
5. **Preferences out of YAML:** per-tenant limits (requests/day, not-before hour, auto-apply of
   customer-facing dims) become client settings the account team can edit, with the policy as the ceiling.
6. **Pilot with one approver** (Kestrel is the natural first: auto-apply allowed, fast approvers). Run a
   weekly review of every applied change against its dry run, to calibrate impact scores.
7. **Then scale out:** shard by workflow, run a load test at 10× on the real hardware, and add the
   cross-tenant credit ledger (C15) before turning on a second tenant's LLM budget.

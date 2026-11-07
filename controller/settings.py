"""Every tunable number in one place. Each one has the reason it has this value.

Nothing here is read from a scenario file. Values were chosen from statistics (sample sizes, error rates) and
from the policy limits the API returns, then checked against the public scenarios' grade reports.
"""

# ---- loop pacing -------------------------------------------------------------------------------------------
TICK_BUSY_MIN = 15        # advance step while any workflow dials or any approver works: an approved change
                          # must be applied inside a 2 h window, and incidents need a reaction inside the hour
TICK_IDLE_MIN = 60        # at night nothing can be decided or dialled, so take bigger steps

# ---- experiments (welcome_message, voice) ------------------------------------------------------------------
EXPLORE_SHARE = 0.05      # share given to each untested option: a -15% arm costs 0.75% of engagement, under
                          # the 1% line that makes a change "harmful"
EXPLORE_CAP = 0.15        # all untested options of one dimension together never get more than this
CHALLENGER_CAP = 0.30     # before promotion, no challenger gets more than this (Thompson can't run away)
MIN_ARM_N = 300           # connected calls per arm before a winner can be declared (≈ +20% lift at 80% power)
P_PROMOTE = 0.95          # P(challenger beats incumbent) needed for a workflow-level promotion
P_PROMOTE_SEGMENT = 0.99  # stricter bar for segment-level claims: many more looks → more false winners
MAX_EXPECTED_LOSS = 0.003 # expected engagement lost if we switch and are wrong (absolute, ≈0.5% relative)
P_DROP_LOSER = 0.95       # P(arm worse than incumbent) at which an arm is taken to 0
HOLDOUT_SHARE = 0.10      # after a promotion the old incumbent keeps this share, so decay can be measured
WINDOW_DAYS = 4           # experiment decisions read only the last N days ...
HALF_LIFE_DAYS = 1.0      # ... and weight each day by 0.5^(age/half-life): novelty wears off, so a flat window
                          # keeps "remembering" good days of a variant that has already stopped working
DECAY_WINDOW_DAYS = 2     # decay check compares winner vs holdout on this recent window
P_DECAY = 0.5             # roll back when P(winner still better than holdout) falls under this ...
DECAY_CHECKS = 2          # ... on this many consecutive checks
MIN_SEGMENT_SHARE = 0.15  # a segment must carry ≥15% of connected calls to become its own cohort
MAX_SEGMENTS = 4          # experiment segments per workflow (cohorts = (segments+1) × schedule groups ≤ 20)
MAX_STEP = 0.10           # a challenger's share moves at most 10 points per review (no whiplash on noise)
HOLD_BAND = (0.35, 0.80)  # inside this band of P(challenger > incumbent) its share doesn't move at all; it
                          # grows only above 0.80 (a -10% arm reaches that by luck ~1% of the time at n≈150)
RECENT_DAYS = 2           # "is it still winning?" gate: yesterday + today
RECENT_MIN_N = 80         # connected calls per arm before the recent-day gate can block an increase
MC_DRAWS = 20000          # Monte Carlo draws for posteriors (seeded, so decisions are deterministic)

# ---- schedule (call time) ----------------------------------------------------------------------------------
SCHEDULE_GROUPS = 3       # at most this many schedule groups (morning / midday / evening shapes)
SCHEDULE_MIN_DIALS = 400  # dials a feature value needs in history before its hour curve is trusted
WEIGHT_MIN, WEIGHT_MAX = 0.4, 2.5   # tilt, never switch off: an omitted / zero hour wastes capacity
SCHEDULE_SHRINK_N = 200   # shrink each hour's rate toward the group mean with this pseudo-count

# ---- caller ID (DID) ---------------------------------------------------------------------------------------
DID_WINDOW_MIN = 180      # look-back for the DID health check
DID_MIN_DIALS = 200       # never judge a number on fewer dials than this (constraint 5: populations)
DID_BAD_RATIO = 0.70      # flagged if its pickup is < 70% of what its peers got in the same hours ...
DID_P = 1e-4              # ... and a one-sided binomial test agrees at this level. Strict on purpose: we check
                          # every number every hour, ~1,000 looks per scenario, so p<0.001 would false-alarm
DID_NEW_SHARE = 0.05      # share for a never-used number while we learn whether it's healthy: small, because
                          # an unknown number can already be spam-flagged and the change would be harmful
DID_NEW_TOTAL = 0.10      # all numbers on probation together never get more than this ...
DID_NEW_TOTAL_SINGLE = 0.25  # ... except when one number carries everything: concentration has a known cost
                             # (a number's pickup falls with its load), so diversifying faster is worth it
DID_PROBATION_MIN_DIALS = 150   # a number on probation is judged on everything since it was introduced ...
DID_PROBATION_BAD_RATIO = 0.75  # ... and dropped if it picks up < 75% of what established numbers get ...
DID_PROBATION_P = 0.01          # ... at this confidence (a looser bar: it's an explicit, cheap trial)
DID_TRUST_DIALS = 250     # dials before a new number can pass probation ...
DID_GOOD_RATIO = 0.90     # ... and it must pick up at ≥ 90% of its peers to get a full share

# ---- hallucination incidents -------------------------------------------------------------------------------
HALLUC_WINDOW_MIN = 240   # flags are counted since the prompt changed, capped at this look-back
HALLUC_MIN_CONNECTED = 150  # minimum connected calls before the detector may fire
HALLUC_P = 0.001          # one-sided binomial p-value to declare a spike (we look every tick, so be strict)
VALIDATION_N = 500        # synthetic conversations per validation run: 5 credits, pass-rate SE ≈ 0.5 pt
INCIDENT_CREDIT_RESERVE = 0.40  # never spend the last 40% of the starting credits on anything but incidents
VERIFY_MIN_CONNECTED = 150      # calls after a fix before we close the incident

# ---- governance --------------------------------------------------------------------------------------------
INCIDENT_SLOTS = 1        # request slots per workflow per 24 h kept free for incidents
APPLY_MARGIN_MIN = 30     # don't send a request whose worst-case decision lands this close to approver close
RETRY_REJECTED_AFTER_MIN = 60 * 18  # a judgement-call rejection is retried with fresh data next day, once

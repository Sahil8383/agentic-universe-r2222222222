# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.395 | +17.3% | 0.264 | +8.4% | 0.529 | +6.6% | 42% | 390 (409) |
| arav_kyc_1 | 0.464 | +29.3% | 0.270 | +25.3% | 0.595 | -0.9% | 3% | 444 (352) |
| arav_renewal_0 | 0.466 | +14.9% | 0.285 | +8.0% | 0.576 | +5.1% | 29% | 486 (408) |
| kest_cancel_2 | 0.389 | +15.7% | 0.264 | +8.8% | 0.522 | +4.6% | 33% | 423 (418) |
| kest_kyc_1 | 0.481 | +35.2% | 0.273 | +27.2% | 0.606 | +1.4% | 10% | 445 (370) |
| kest_renewal_0 | 0.476 | +18.3% | 0.284 | +7.2% | 0.592 | +8.7% | 44% | 452 (403) |
| nort_cancel_2 | 0.389 | +14.6% | 0.263 | +7.8% | 0.522 | +4.8% | 34% | 451 (368) |
| nort_kyc_1 | 0.415 | +16.6% | 0.243 | +12.9% | 0.600 | +0.7% | 3% | 422 (318) |
| nort_renewal_0 | 0.469 | +15.8% | 0.282 | +6.8% | 0.583 | +6.4% | 34% | 432 (419) |
| tide_cancel_2 | 0.389 | +13.4% | 0.264 | +8.0% | 0.521 | +4.1% | 32% | 458 (401) |
| tide_kyc_1 | 0.413 | +15.9% | 0.242 | +12.7% | 0.598 | +0.0% | 3% | 394 (376) |
| tide_renewal_0 | 0.474 | +17.1% | 0.284 | +7.4% | 0.591 | +8.2% | 43% | 433 (425) |
| ALL | 0.435 | +18.6% | 0.268 | +11.3% | 0.570 | +4.4% | 25% | 5230 (4667) |

## Changes applied by the controller

88 applied — neutral: 35, beneficial: 53

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.010 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.001 didΔ=+0.008 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.010 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.008 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.010 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.001 didΔ=+0.008 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.010 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.001 didΔ=+0.008 → **neutral**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- tide_cancel_2 v3 (controller_approved) dims=['cohorts', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.035 → **beneficial**
- kest_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.025 → **beneficial**
- tide_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.025 → **beneficial**
- kest_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.010 → **neutral**
- tide_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.010 → **neutral**
- nort_cancel_2 v3 (controller_approved) dims=['cohorts', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.025 → **beneficial**
- tide_renewal_0 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.036 → **beneficial**
- arav_cancel_2 v3 (controller_approved) dims=['cohorts', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.010 → **neutral**
- arav_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.025 → **beneficial**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.056 → **beneficial**
- arav_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.011 → **neutral**
- arav_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.038 → **beneficial**
- kest_cancel_2 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.007 didΔ=+0.147 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.007 didΔ=+0.164 → **beneficial**
- nort_cancel_2 v6 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v6 (controller_auto) dims=['welcome_message'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- tide_cancel_2 v6 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- tide_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.116 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.117 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['voice'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.008 didΔ=+0.142 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.007 didΔ=+0.155 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.225 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.150 → **beneficial**
- nort_cancel_2 v7 (controller_auto) dims=['voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v7 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v7 (controller_auto) dims=['voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.115 → **beneficial**
- arav_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.174 → **beneficial**
- nort_cancel_2 v8 (controller_auto) dims=['voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v8 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v8 (controller_auto) dims=['voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v9 (controller_auto) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v9 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v9 (controller_auto) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v10 (controller_auto) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- arav_cancel_2 v10 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v10 (controller_auto) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nort_renewal_0 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.117 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.131 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v11 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.022 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v11 (controller_auto) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v12 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v11 (controller_auto) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.173 → **beneficial**
- kest_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.126 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.173 → **beneficial**
- arav_cancel_2 v13 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v12 (controller_auto) dims=['voice'] engageΔ=+0.008 didΔ=+0.000 → **neutral**
- arav_cancel_2 v14 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- arav_cancel_2 v15 (controller_auto) dims=['welcome_message', 'voice'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- tide_renewal_0 v8 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.011 didΔ=+0.123 → **beneficial**
- kest_cancel_2 v8 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v8 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.012 didΔ=+0.143 → **beneficial**
- arav_cancel_2 v16 (controller_auto) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_kyc_1 v6 (controller_approved) dims=['voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- arav_kyc_1 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.064 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v7 (controller_approved) dims=['welcome_message'] engageΔ=-0.000 didΔ=+0.000 → **neutral**
- nort_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v9 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v9 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.024 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.113 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 50,
  "decisions": {
    "approved": 42,
    "rejected": 6,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 6
  },
  "applied_via_approval": 42,
  "auto_applied": 46,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_renewal_0": 1,
    "nort_kyc_1": 2,
    "nort_cancel_2": 1,
    "arav_kyc_1": 2,
    "arav_cancel_2": 1,
    "tide_kyc_1": 2,
    "tide_cancel_2": 1,
    "arav_renewal_0": 2,
    "tide_renewal_0": 2,
    "kest_kyc_1": 2,
    "kest_renewal_0": 1,
    "kest_cancel_2": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "kest_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.95,
    "4": 0.987
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 1.0,
    "4": 1.0
  },
  "nort_renewal_0:welcome_message=W3": {
    "4": 0.976
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0
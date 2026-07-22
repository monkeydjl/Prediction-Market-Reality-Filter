# LoL integration gates (ADR-004 P1–P8)

Status legend: `[ ]` open · `[x]` done

| Gate | Description | Status | Notes (no secrets) |
|------|-------------|--------|--------------------|
| P1 | v1 leagues in scope | [ ] | e.g. LCK, LPL, LEC, Worlds — list here when decided |
| P2 | Schedule source docs | [ ] | endpoint list, auth method name, rate limit, timezone |
| P3 | Result/settlement source | [ ] | same as P2 or conflict policy |
| P4 | Team identity map rules | [ ] | external_id → display name → market slug |
| P5 | Markets v1 = series winner only | [x] | ADR-004 locked |
| P6 | ToS / license OK for cache+display | [ ] | legal owner sign-off date |
| P7 | Empty-state UX copy | [ ] | FE strings reviewed |
| P8 | Contract tests + dry-run settle sample | [ ] | Contract tests land with dry-run stack (Tasks 3–6); production settle sample still open |

**Rule:** Do not merge production HTTP schedule client until P2, P3, P6 are `[x]`.

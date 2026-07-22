# LoL integration gates (ADR-004 P1–P8)

**Vendor selection:** [ADR-005](../adr/005-lol-data-vendor-selection.md) (Accepted 2026-07-22)  
**Status legend:** `[ ]` open · `[x]` done · `[~]` decided / partial (not production-unblocked)

| Gate | Description | Status | Notes (no secrets) |
|------|-------------|--------|--------------------|
| P1 | v1 leagues in scope | **[x]** | **In:** `lol_lck`, `lol_lpl`, `lol_lec`, `lol_worlds`, `lol_msi`. **Out of v1:** LCS/CBLOL/PCS/VCS academy/amateur/showmatch (new codes later). Frozen in ADR-005 D5. |
| P2 | Schedule source docs | **[~]** | **Selected class:** official partner feed — **preferred GRID** (commercial LoL series, not Open Access). Endpoint names, auth scheme, rate limit, timezone: **fill after sandbox access** (procurement checklist ADR-005). Until then production HTTP client must not merge. Interim: dry-run JSON + Null source only. |
| P3 | Result/settlement source | **[~]** | **Same primary as schedule** (ADR-005 D1/D6). Conflict: primary partner wins; bookmaker/community may log discrepancy only; grace `LOL_SETTLE_GRACE_HOURS` default 6 (config when client lands). Equal unfinished scores → `None` (implemented). Production settle sample still open until sandbox. |
| P4 | Team identity map rules | **[x]** | See **Identity rules** below. |
| P5 | Markets v1 = series winner only | **[x]** | ADR-004 locked; engine `lol_market_only`. |
| P6 | ToS / license OK for cache+display | **[ ]** | **Blocked on legal.** Prefer GRID commercial license that allows cache + display + offline model inference. GRID Open Access ≠ LoL. Owner: Legal + Product. Sign-off date: _TBD_. |
| P7 | Empty-state UX copy | **[x]** | Landing `coming_soon`: 「不会展示占位赔率或模拟结果」; Kernel empty: 「今日无比赛」 / match-count zero hints; boundary docs linked. Review owner: FE (2026-07-22). |
| P8 | Contract tests + dry-run settle sample | **[~]** | **Dry-run / unit:** `test_lol_adapter` (import, sync_schedule, fetch_outcome synthetic `lol-dry-lck-001`), feature/engine/registry tests. **Production vendor contract tests:** open until sandbox. |

**Hard rule:** Do **not** merge production HTTP schedule client until **P2, P3, and P6 are fully `[x]`** (not merely `[~]`). P2/P3 become `[x]` only when non-secret endpoint tables + auth method name + rate limit + timezone are written below **and** legal has signed P6.

---

## Identity rules (P4) — frozen

| Layer | Rule |
|-------|------|
| External series id | Vendor-stable series/match id; Kernel `match_id = "lol-" + external_id` (alnum + hyphen) |
| Team external id | Prefer vendor team id; store in fixture metadata / future identity table |
| Display name | Vendor `name` at ingest time; UI may show slug fallback |
| `home_code` / `away_code` | Uppercase short code ≤ 8 chars from vendor abbrev or first letters |
| Market slug (Phase 7 later) | Lowercase, hyphenated `{team_a}-vs-{team_b}` + date; **not** required for v1 market-only engine |
| Roster churn | Do not invalidate historical series; new series use current names; no retro rewrite |
| Competition | Map vendor tournament → one of P1 codes; unknown → drop from v1 sync (log warn) |

---

## Schedule source sheet (P2) — complete when sandbox granted

| Field | Value |
|-------|--------|
| Vendor | GRID (preferred) / _alt after legal_ |
| Product name | _e.g. Series Events / commercial LoL feed — from contract_ |
| Auth | _API key / OAuth — method name only_ |
| Base URL | _from vendor docs — no secrets_ |
| List upcoming | _path or GraphQL operation_ |
| Get result | _path or operation_ |
| Rate limit | _req/min_ |
| Timezone | Prefer UTC in storage; convert vendor TZ at ingest |
| Cancel / forfeit | _semantics from vendor docs_ |
| Last docs review | _YYYY-MM-DD_ |

---

## Result source sheet (P3)

| Field | Value |
|-------|--------|
| Same as schedule? | **Yes (target)** |
| Lag SLA | _from contract_ |
| Conflict policy | ADR-005 D6 |
| Grace hours config | `LOL_SETTLE_GRACE_HOURS` default **6** (shell only until client) |
| Operator override | Signed JSON import only; audit log required |

---

## Procurement log (no secrets)

| Date | Action | Who | Result |
|------|--------|-----|--------|
| 2026-07-22 | ADR-005 vendor class selected (GRID primary) | Eng/docs | Accepted |
| _TBD_ | Commercial access request | Product | |
| _TBD_ | Legal ToS sign-off | Legal | → P6 `[x]` |

---

## Production unblock checklist

- [ ] P2 table fully filled  
- [ ] P3 table fully filled  
- [ ] P6 legal date entered  
- [ ] Sandbox key in secret store (not git)  
- [ ] `PartnerHttpLolScheduleSource` plan + contract tests green  
- [ ] Operator decision on `PHASE_LOL_ENABLED` for staging only first  

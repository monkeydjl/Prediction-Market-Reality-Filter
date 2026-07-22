# ADR-005: LoL schedule & result data vendor selection

**Status**: Accepted (selection & procurement path)  
**Date**: 2026-07-22  
**Related**: [ADR-004](004-esports-data-adapter.md), [GATES.md](../lol/GATES.md), [ESPORTS_BOUNDARY.md](../ESPORTS_BOUNDARY.md), plan `docs/superpowers/plans/2026-07-22-lol-esports-adapter.md`

## Context

ADR-004 requires a **trusted official/partner** schedule + result feed before any production HTTP `LolScheduleSource`. Dry-run JSON import already works; this ADR freezes **which vendor class we pursue** and how gates P2/P3/P6 close.

Product constraints (unchanged):

- No auto-betting; no fake markets
- Settlement must not invent series scores
- Community scrapers / bookmaker title parsing are **not** sole truth sources
- Secrets never committed

Research snapshot (public product pages, 2026-07-22; no credentials acquired):

| Candidate | Nature | LoL schedule/results | Settlement fitness | Notes |
|-----------|--------|----------------------|--------------------|-------|
| **GRID** (grid.gg) | Official esports data platform; publisher/TO partnerships | Commercial feeds; documented LoL event tracking (e.g. MSI) and broadcast/betting products | **High** — positions as official telemetry / official series data | Open Access free tier is **CS2 + Dota 2 only**; LoL series is **not** free Open Access. “Series Events” called out as paid product. |
| **PandaScore** | Commercial esports odds + stats API | Broad LoL coverage historically for odds/stats | **Medium–High** for markets; schedule/result authority is **vendor contract–dependent**, not Riot-official by default | Strong for Phase 7 odds enrichment; weaker as sole settlement unless license says so |
| **Riot public developer APIs** | Player/game APIs | **No** documented public “esports schedule + settle” product for third-party Kernel use | **N/A** for our need | Do not invent unofficial `lolesports.com` private APIs as production source |
| **Leaguepedia / scrapers** | Fan wiki / HTML | Unofficial | **Rejected** as sole schedule/settle | May inform human ops; never auto-settle |
| **Bookmaker titles only** | Derived | Incomplete / lagging | **Rejected** as sole truth | May assist matching later |

## Decision

### D1 — Primary schedule + settlement vendor class: **official partner feed (GRID-class)**

**Primary path:** procure a **written commercial license** for LoL **series schedule + series results** from an **official / rights-aligned** data platform. **Preferred shortlist #1: GRID** (or successor equivalent under same rights model).

Rationale:

- Aligns with ADR-004 D3 (“official/partner API”)
- Settlement and schedule should share one authority to minimize conflicts
- Public materials emphasize official in-game / TO partnerships and betting-grade feeds

### D2 — Secondary / enrichment: **commercial stats-odds vendor (PandaScore-class)**

**Secondary path (optional, post-primary):** PandaScore-class API for **market probabilities / depth**, never as sole settlement source unless the same license explicitly covers authoritative series outcomes.

### D3 — Explicitly rejected for production sole source

- Unofficial Riot esports HTTP endpoints reverse-engineered from fan sites
- Wiki scrape (Leaguepedia) as automated truth
- Bookmaker event titles as only schedule
- GRID Open Access **as LoL source** (titles limited to CS2/Dota 2 per public Open Access FAQ)

### D4 — Integration architecture (when license exists)

Implement behind existing Protocol (no change to Kernel core):

```text
LolScheduleSource (Protocol)
  ├── NullLolScheduleSource          # default
  ├── DryRunFileSource               # LOL_DRY_RUN_IMPORT (already)
  └── PartnerHttpLolScheduleSource   # NEW after P2/P3/P6 closed
        └── maps vendor series → LolSeriesRecord
              match_id = lol-{external_id}
```

Config (names fixed at implement time; defaults empty/false):

| Env | Purpose |
|-----|---------|
| `LOL_SCHEDULE_VENDOR` | `null` \| `dry_run` \| `grid` \| `pandascore` (or vendor id) |
| `LOL_VENDOR_API_BASE` | Base URL from vendor docs (no secret) |
| `LOL_VENDOR_API_KEY` | Secret via env/secret store only |
| `PHASE_LOL_ENABLED` | Still required; vendor alone does not open product |

### D5 — League code freeze (v1)

| competition_code | Scope |
|------------------|--------|
| `lol_lck` | LCK (Korea major) |
| `lol_lpl` | LPL (China major) |
| `lol_lec` | LEC (EMEA major) |
| `lol_worlds` | World Championship |
| `lol_msi` | Mid-Season Invitational (international) |

**Out of v1:** LCS/CBLOL/PCS/VCS secondaries, academy, amateur, showmatches (may map later under new codes).

### D6 — Settlement conflict policy

1. **Primary series result** from partner feed (same as schedule vendor when possible).  
2. If primary missing after `LOL_SETTLE_GRACE_HOURS` (default **6** once config lands): operator may import signed result JSON; still no invent.  
3. Bookmaker / community scores may **flag discrepancy** in ops logs; they **never auto-overwrite** primary.  
4. Equal map/series scores unfinished → `fetch_outcome` stays `None` (already coded).

### D7 — What this ADR does **not** do

- Does **not** sign a contract or store API keys  
- Does **not** enable `PHASE_LOL_ENABLED` in production  
- Does **not** merge production HTTP client code until GATES **P2, P3, P6** are `[x]` with **legal owner** named on P6

## Procurement checklist (closes P6)

Owner roles (assign names in GATES notes):

| Step | Owner role | Done when |
|------|------------|-----------|
| 1. Request GRID (or equiv.) commercial LoL series access | Product / Biz | Ticket / email logged (no secrets in repo) |
| 2. Confirm titles: LCK, LPL, LEC, Worlds, MSI | Product | Written scope in GATES P1 notes |
| 3. Confirm license allows **cache + display + offline predict** (not just betting B2B) | Legal | Signed ToS/DPA summary date in GATES P6 |
| 4. Rate limit + SLA + timezone documented | Eng | P2 table filled with non-secret endpoint names |
| 5. Result feed lag + cancel/forfeit semantics | Eng + Legal | P3 notes |
| 6. Sandbox key in secret store (not git) | Ops | Runbook secret path only |

## Consequences

- ✅ Clear preferred vendor class; dry-run remains default until license  
- ✅ Avoids building on unofficial Riot endpoints that break ToS  
- ⚠️ LoL Open Access is **not** a free shortcut — budget for commercial LoL data  
- ⚠️ P6 remains open until legal signs; production HTTP stays blocked by GATES rule  
- ⚠️ PandaScore may still be needed for market features; keep dual-vendor mapping careful

## Alternatives considered

| Option | Verdict |
|--------|---------|
| Ship production on unofficial lolesports JSON | Rejected (ToS / stability) |
| Leaguepedia-only | Rejected (settlement) |
| PandaScore as sole source | Rejected as **sole** settle; allowed as enrichment |
| Wait for free Riot public esports API | Deferred indefinitely; not available for our use case today |
| GRID Open Access for LoL | Rejected — public OA is CS2/Dota 2 |

## Follow-up implementation (next plan, not this ADR)

1. Close P6 with legal sign-off date in GATES  
2. Plan: `PartnerHttpLolScheduleSource` + contract tests against vendor sandbox  
3. Map vendor tournament IDs → `lol_*` competition codes  
4. Only then consider operator-enabled `PHASE_LOL_ENABLED` in non-prod

## References

- https://grid.gg/ — official esports data platform  
- https://grid.gg/open-access/ — OA titles CS2/Dota 2; Series Events paid  
- https://pandascore.co/ — commercial esports odds/stats  
- ADR-004 decisions D1–D3  
- `backend/app/sports/lol/source.py` Protocol  

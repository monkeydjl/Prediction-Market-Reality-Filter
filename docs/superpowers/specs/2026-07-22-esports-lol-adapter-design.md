# Design: LoL esports adapter (ADR-004 Accepted)

**Date**: 2026-07-22  
**Status**: Design frozen with ADR-004; **implementation blocked** on data-source gates  
**Canonical decision**: [docs/dev/adr/004-esports-data-adapter.md](../../dev/adr/004-esports-data-adapter.md)

## Goal

Define how the 竞猜 module will eventually host **League of Legends** predictions
on the existing Sports Prediction Kernel without fake markets or wrong-sport engines.

## Non-goals (this design / ADR cycle)

- Writing `LolAdapter` or engines now
- Choosing a commercial vendor by name (requires procurement / ToS review)
- CS2 / Dota / multi-title unified engine
- Auto-betting

## Product decisions (approved)

1. **First title**: LoL  
2. **Data**: Official/partner API gate before production  
3. **Encoding**: `sport=lol`, prefix `lol-`  
4. **Depth this cycle**: Accepted ADR + checklist only (no skeleton code)

## System placement

Reuse MultiAdapter registration pattern used by NBA/MLB:

- New adapter behind `PHASE_LOL_ENABLED` (default OFF)
- Schedule: `fetch_schedule` / `sync_schedule` with `ScheduleFilter(sport="lol")`
- Catalog: keep `esports` umbrella until implement; then `kernel` + optional `lol` card
- UI: empty states only; never invent odds

## Series model (v1)

- One Kernel match = **one series** (Bo1/Bo3/Bo5 stored in metadata)
- Primary market: series winner (two teams)
- Maps / drafts: metadata or v2 markets

## Risks

| Risk | Mitigation |
|------|------------|
| No API yet | Hard gate; product blocked honestly |
| Version patches shift strength | Engine research spike before non-market model |
| Roster churn | Identity map + short half-life ratings |
| Settlement disputes | Written conflict policy in P3 |

## Success criteria for “implementation unblocked”

ADR checklist P1–P8 complete and reviewed; then open a writing-plans task for code.

## Open points deferred to implementation plan

- Exact league code table (`lol_lck` vs …)
- Vendor shortlist
- Whether hub keeps single `esports` card or splits `lol`
- Engine v0 = market-only vs simple rating

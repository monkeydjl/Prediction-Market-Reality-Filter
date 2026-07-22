# Esports / 电竞 — product boundary (竞猜 module)

**Status**: **coming_soon** placeholder in product UI.  
**Architecture decision**: [ADR-004](adr/004-esports-data-adapter.md) — **Accepted** (2026-07-22).

Do **not** invent odds, fixtures, or settlement rules in code until the ADR
prerequisites checklist is complete and `PHASE_LOL_ENABLED` is intentionally ON.

## Accepted decisions (summary)

| Topic | Decision |
|-------|----------|
| First title | **League of Legends (LoL)** only for v1 |
| Kernel sport | `lol` |
| match_id prefix | `lol-` |
| Data source | **Trusted official/partner API** before production adapter |
| Flag | `PHASE_LOL_ENABLED` default **OFF** (to be added at implementation) |
| Engines | Dedicated LoL path — **no** football/NBA engine reuse |

See ADR-004 for full checklist (P1–P8) and implementation order.  
Implementation plan: [2026-07-22-lol-esports-adapter.md](../superpowers/plans/2026-07-22-lol-esports-adapter.md).

## What is in scope later

- Same high-level workflow as Kernel sports: match → features → prediction → edge vs market → settle → learn.
- Catalog entry already exists (`id=esports`, track=`placeholder`) in:
  - FE: `frontend/src/lib/betting/competition-catalog.ts`
  - BE: `backend/app/kernel/betting_catalog.py` + `GET /api/betting/catalog`
- After gates: MultiAdapter `lol-` + `GET /api/predictions/matches?sport=lol`.

## What is explicitly out of scope until data sources exist

- Hard-coded fake matches or “demo” prices on `/sports/betting/esports`
- Pretending esports maps to football/NBA engines without a dedicated adapter
- Auto-enabling Kernel flags for esports / LoL
- CS2 / Dota as v1 (future per-title sports: e.g. `cs2-`)
- Settlement from bookmaker title scrape alone

## Prerequisites before implementation (must complete)

1. Title / league list for LoL v1 (which regions) — **title chosen: LoL**
2. Trusted schedule + result feed (or written partner contract)
3. Market mapping rules (v1: series winner only)
4. Settlement truth source and conflict policy
5. ADR-004 Accepted ✅ — remaining rows in ADR checklist P1–P8

## Interim UX

- Hub card → `/sports/betting/esports` explains “no fake markets”
- No schedule list, no Edge detect, no recommendation for esports until prerequisites land
- Operator schedule sync (`POST /api/predictions/schedule/sync`) does **not** include LoL until flag + adapter exist

## Operator note

Kernel football/NBA leagues use existing flags (`PHASE2_*`, `PHASE4_NBA_*`, …).
LoL is a **separate** flag and prefix; turning on football flags never enables esports.

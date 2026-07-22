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
| Flag | `PHASE_LOL_ENABLED` exists in config; default **OFF** |
| Dry-run | `LOL_DRY_RUN_IMPORT` + series JSON path (no production vendor HTTP) |
| Engines | Dedicated LoL path (`lol_market_only`) — **no** football/NBA engine reuse |

See ADR-004 for full checklist (P1–P8) and implementation order.  
Implementation plan: [2026-07-22-lol-esports-adapter.md](../superpowers/plans/2026-07-22-lol-esports-adapter.md).  
Gates: [docs/dev/lol/GATES.md](lol/GATES.md).  
Ops: [RUNBOOK — LoL esports](../ops/RUNBOOK.md).

## What is in scope later

- Same high-level workflow as Kernel sports: match → features → prediction → edge vs market → settle → learn.
- Catalog entries:
  - Legacy hub: `id=esports`, track=`placeholder`
  - LoL: `id=lol` / sport=`lol` in FE catalog + BE `betting_catalog` + `GET /api/betting/catalog` (`phase_lol_enabled` flag)
  - FE: `frontend/src/lib/betting/competition-catalog.ts`
  - BE: `backend/app/kernel/betting_catalog.py`
- With dry-run stack (flag intentionally ON + import path): MultiAdapter `lol-` + `GET /api/predictions/matches?sport=lol`.
- Production vendor HTTP still blocked until GATES P2/P3/P6 are checked.

## What is explicitly out of scope until data sources exist

- Hard-coded fake matches or “demo” prices on `/sports/betting/esports`
- Pretending esports maps to football/NBA engines without a dedicated adapter
- Auto-enabling Kernel flags for esports / LoL
- CS2 / Dota as v1 (future per-title sports: e.g. `cs2-`)
- Settlement from bookmaker title scrape alone
- Production schedule HTTP client while `PHASE_LOL_ENABLED` stays default OFF or GATES open

## Prerequisites before production enablement

1. Title / league list for LoL v1 (which regions) — **title chosen: LoL**
2. Trusted schedule + result feed (or written partner contract)
3. Market mapping rules (v1: series winner only) — locked in ADR-004 / GATES P5
4. Settlement truth source and conflict policy
5. ADR-004 Accepted ✅ — remaining rows in [GATES.md](lol/GATES.md) P1–P8 (esp. P2/P3/P6)

## Interim UX

- Hub card → `/sports/betting/esports` explains “no fake markets”
- LoL catalog row stays `coming_soon` / `placeholder` until product promotes track
- No Edge detect / recommendation for esports until prerequisites land
- Operator schedule sync for LoL is **dry-run only** until GATES allow production HTTP:
  - `PHASE_LOL_ENABLED=true` + `LOL_DRY_RUN_IMPORT=true` + series JSON path
  - `POST /api/predictions/schedule/sync?sport=lol` (write key)

## Operator note

Kernel football/NBA leagues use existing flags (`PHASE2_*`, `PHASE4_NBA_*`, …).
`PHASE_LOL_ENABLED` is a **separate** flag and `lol-` prefix; turning on football
flags never enables esports. Leave LoL OFF in production until GATES and ops
review; dry-run import path is for local/dev verification only.

# Esports / 电竞 — product boundary (竞猜 module)

Status: **coming_soon** placeholder only. Do not invent odds, fixtures, or settlement rules in code.

## What is in scope later

- Same high-level workflow as Kernel sports: match → features → prediction → edge vs market → settle → learn.
- Catalog entry already exists (`id=esports`, track=`placeholder`) in:
  - FE: `frontend/src/lib/betting/competition-catalog.ts`
  - BE: `backend/app/kernel/betting_catalog.py` + `GET /api/betting/catalog`

## What is explicitly out of scope until data sources exist

- Hard-coded fake matches or “demo” prices on `/sports/betting/esports`
- Pretending esports maps to football/NBA engines without a dedicated adapter
- Auto-enabling Kernel flags for esports

## Prerequisites before implementation

1. **Title / league list** (e.g. LoL, CS2, Dota) and identifier scheme
2. **Trusted schedule + result feed** (or operator import format)
3. **Market mapping rules** (best-of series, maps, handicap)
4. **Settlement truth** source and conflict policy
5. Optional: ADR under `docs/dev/adr/` for esports data adapter

## Interim UX

- Hub card → `/sports/betting/esports` explains “no fake markets”
- No schedule list, no Edge detect, no recommendation for esports until prerequisites land

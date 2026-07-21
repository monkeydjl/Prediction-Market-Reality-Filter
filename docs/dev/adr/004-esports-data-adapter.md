# ADR-004: Esports data adapter (竞猜 module)

**Status**: Proposed  
**Date**: 2026-07-21  
**Related**: `docs/dev/ESPORTS_BOUNDARY.md`, Kernel MultiAdapter, betting catalog `id=esports`

## Context

The 竞猜 hub already lists **电竞** as `coming_soon` / `placeholder`. We need a
decision frame before writing adapters, engines, or fake fixtures.

Kernel today is sport-prefix MultiAdapter (`wc-`, `epl-`, `nba-`, …) with
match → features → engine → settle. Esports titles differ on:

- Series format (Bo1/Bo3/Bo5), map picks, roster swaps
- Identity (org vs lineup vs player)
- Market types (match winner, map handicap, totals)
- Result authority (official API vs tournament pages vs bookmakers)

## Decision

1. **Do not** implement a production esports adapter until prerequisites in
   `ESPORTS_BOUNDARY.md` are met (title list, schedule feed, market map,
   settlement truth).
2. **First title** should be chosen explicitly (recommended starting point:
   **CS2** or **LoL** — single primary region, stable API or partner feed).
3. Architecture when ready:
   - New sport code `esports` (or per-title sports `lol` / `cs2` if engines diverge).
   - Prefixes e.g. `cs2-`, `lol-` registered on MultiAdapter like NBA.
   - Dedicated `EsportsFeatureBuilder` + engine(s); **no** reuse of football
     Elo-odds without an explicit research spike.
   - Catalog `track` moves from `placeholder` → `kernel` only after flag
     `PHASE_ESPORTS_ENABLED` (name TBD) defaults **OFF**.
4. Markets: Polymarket / traditional odds bridge reuses Phase 7 patterns after
   match identity is stable; settlement must not invent map scores.

## Consequences

- ✅ Avoids fake odds and premature Kernel coupling.
- ✅ Clear flag gate and prefix scheme when implementation starts.
- ⚠️ Product must pick first title and feed before coding.
- ⚠️ Ranking / strength models need title-specific research (not Elo football).

## Alternatives considered

- **Map esports onto football MultiFactor**: rejected — feature semantics do not transfer.
- **Separate microservice**: deferred; Kernel path is enough for v1 if one title.
- **Manual CSV-only ingest**: acceptable as **bootstrap** for dry-run, not as
  production truth without settlement policy.

## Implementation checklist (when unblocked)

1. ADR update → Accepted + chosen title/feed.
2. Config flag default OFF + MultiAdapter registration.
3. Fixture schema (series, maps, start time, teams).
4. Engine v0 (market-only or simple rating) + empty-state UI.
5. Remove `coming_soon` copy; set catalog `adapter_likely` from new flag.

# Remove Manifold Channel Design

Date: 2026-07-08

## Goal

Stop using Manifold as an active event channel while preserving historical records and audit continuity.

The product should no longer:

- discover new Manifold events,
- auto-resolve events from Manifold market APIs,
- direct-settle historical Manifold source IDs,
- advertise Manifold as a current supported source in frontend market links or user-facing docs.

Historical events whose `source.platform` is `Manifold` remain in the event store unchanged.

## Non-goals

- Do not delete or rewrite existing Manifold events in `event_store.json`.
- Do not remove historical review notes, old milestone docs, or past audit records merely because they mention Manifold.
- Do not remove Polymarket, Kalshi, Open Web, World Cup, or Metaculus support.
- Do not introduce a migration that changes calibration history.

## Recommended Approach

Use a product-layer removal with historical compatibility:

1. Remove Manifold from active discovery.
2. Remove Manifold from active auto-resolution.
3. Remove Manifold frontend entry points.
4. Keep old Manifold data readable.
5. Update current user-facing docs and tests.

This is safer than deleting every Manifold module immediately because historical records may still contain `source.platform = "Manifold"`. Keeping those records readable avoids breaking audit, calibration, and debugging flows.

## Backend Design

### Discovery

In `backend/app/services/event_intelligence_service.py`, remove Manifold from the candidate source list.

Expected source list after the change:

- Polymarket
- Kalshi
- optional Polymarket Crypto
- optional World Cup
- optional Metaculus
- Open Web

Discovery status payloads should no longer include a `Manifold` source entry for new runs.

The "no candidates" error message should no longer tell operators to check Manifold.

### Auto-resolution

In `backend/app/services/event_resolve_service.py`, remove Manifold from active resolved-market fetches.

Disable/remove the Manifold direct-settle path that fetches specific Manifold market IDs. Existing Manifold events can remain unresolved or be handled manually if needed, but the system should not call Manifold APIs anymore.

### Configuration

In `backend/app/core/config.py`, remove Manifold from current source weights and current source documentation.

Manifold environment variables can either be removed or treated as legacy/no-op. Prefer no-op compatibility if removing them would break startup for users who still have them in `.env`.

### Candidate deduplication

In `backend/app/services/candidate_dedup_service.py`, remove Manifold from active source priority ordering.

The remaining ordering should prefer stronger market sources and known curated sources over Open Web. Exact final priority can stay close to the current behavior:

1. Polymarket
2. Kalshi
3. Open Web / other configured sources

Historical Manifold records do not pass through candidate deduplication unless a future bug reintroduces Manifold candidates, which tests should prevent.

## Frontend Design

### Detail market links

In `frontend/src/components/detail/market-links.tsx`, remove the Manifold search link.

The detail view should continue to show the event's actual source platform text if an existing historical event came from Manifold, but should not offer a Manifold search/action link.

### Discovery status UI

No special UI migration is needed. Once the backend source list no longer includes Manifold, the existing discovery status renderer will naturally stop showing Manifold for new discovery runs.

## Documentation Design

Update current user-facing docs and active architecture docs that describe supported current sources.

Do update:

- `README.md`
- current user guides under `docs/user/`
- current architecture/dev docs if they present active source lists

Do not churn old review archives or milestone history unless a specific current instruction there would mislead operators.

## Testing Strategy

### Backend tests

Add or update tests to prove:

- discovery source collection no longer calls `manifold_event_source.fetch_candidate_events`;
- discovery status/source summaries do not include Manifold;
- auto-resolve does not call Manifold resolved/direct-fetch functions;
- candidate dedup priority no longer includes Manifold as an active preferred source.

Existing tests that intentionally validate historical Manifold records may be changed to use Kalshi/Polymarket or kept only if they are explicitly about historical compatibility.

### Frontend tests

Add/update tests to prove:

- `market-links.tsx` does not render a Manifold link;
- Polymarket and Kalshi links still render.

### Verification

Run targeted backend and frontend tests first, then broader checks:

```powershell
cd backend
python -m unittest tests.test_event_intelligence_service tests.test_event_resolve_service tests.test_candidate_dedup_service
python -m compileall app

cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```

Adjust exact test files based on the final touched files.

## Rollout Notes

- Existing Manifold records remain visible as historical data.
- New discovery runs should not create Manifold records.
- If an operator sees old Manifold entries, that is expected historical data, not active channel support.
- If the backend is already running, restart it after the change so the active source list is updated.

## Risks

- Removing Manifold auto-resolution means historical Manifold events will not settle via Manifold APIs. This is accepted for this design because the user chose to preserve history but stop the channel.
- Some old docs/tests may still mention Manifold as history. Only current support surfaces should be cleaned.
- If `.env` still contains Manifold variables, they should not re-enable the channel.

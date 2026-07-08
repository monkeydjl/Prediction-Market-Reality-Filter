# On-Chain Prediction Source Adapters Phase 2 Design

Date: 2026-07-08

## Goal

Add real candidate-discovery adapters for the requested on-chain prediction-market sources where an official interface is currently verified, while keeping unverified sources fail-closed.

Phase 2 should move beyond UI visibility for:

- Limitless — Base
- Opinion — BNB Chain
- Predict.fun — BNB Chain

Probable remains planned-only until an official API, indexer, or contract-event interface is verified.

## Verified source-interface status

### Limitless

Status: implementable as a public adapter.

Verified public API:

- Docs: `https://docs.limitless.exchange/api-reference/introduction`
- Active markets endpoint: `GET https://api.limitless.exchange/markets/active`

The adapter can be enabled without credentials, because public market/orderbook endpoints are documented as public.

### Opinion

Status: implementable as an API-key gated adapter.

Verified Open API:

- Docs: `https://docs.opinion.trade/developer-guide/opinion-open-api`
- Authentication: `https://docs.opinion.trade/developer-guide/opinion-open-api/authentication`
- Market endpoint: `GET https://openapi.opinion.trade/openapi/market`

The adapter must remain disabled unless `OPINION_API_KEY` is configured. Requests must send the key via the documented `apikey` header.

### Predict.fun

Status: implementable as an API-key gated adapter.

Verified beta API:

- Docs root: `https://dev.predict.fun/`
- Production base URL: `https://api.predict.fun`
- Markets endpoint: `GET /v1/markets`
- Authentication header: `x-api-key`

The adapter must remain disabled unless `PREDICT_FUN_API_KEY` is configured.

### Probable

Status: planned-only; do not implement a live adapter in Phase 2.

Observed state:

- Requested homepage: `https://probable.finance/`
- Current fetch through web tooling returned an unavailable/502-style response.
- No official API, indexer, or contract-event documentation was verified.

Phase 2 must not scrape the homepage, infer private APIs, or fabricate markets for Probable.

## Architecture

Add a small family of source adapters following the existing `*_event_source.py` pattern:

- Each adapter exports `fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]`.
- Each adapter converts official market records into the existing candidate-event shape consumed by `event_intelligence_service`.
- Each adapter fails closed by returning `[]` on missing credentials, missing endpoint config, HTTP failure, malformed payloads, or unsupported market shapes.
- Each adapter is thin: fetch, eligibility filter, normalizer. No LLM calls, no persistence, no auto-resolution.

Discovery integration should add only sources that can produce verified candidate events:

- Limitless can be included by default if `LIMITLESS_SOURCE_ENABLED=true`.
- Opinion can be included only when `OPINION_API_KEY` is set and `OPINION_SOURCE_ENABLED=true`.
- Predict.fun can be included only when `PREDICT_FUN_API_KEY` is set and `PREDICT_FUN_SOURCE_ENABLED=true`.
- Probable remains absent from `_collect_candidate_events()`.

## Candidate-event contract

All new adapters must emit the same shape as current prediction-market sources:

```python
{
    "question": str,
    "baseline_probability": float,  # 0-100
    "volume": float,
    "liquidity": float,
    "source": {
        "type": "prediction_market",
        "platform": str,
        "source_id": str,
        "question": str,
        "baseline_probability": float,
        "liquidity": float,
        "volume": float,
        "url": str,
        "chain": str,
    },
}
```

If an API returns a probability as `0-1`, normalize to `0-100`. If an API returns `0-100`, preserve it. If the schema is ambiguous, the adapter must skip that market until a field mapping is verified by tests or live inspection.

## Configuration

Add explicit settings with safe defaults:

```python
LIMITLESS_SOURCE_ENABLED: bool = True
LIMITLESS_API_URL: str = "https://api.limitless.exchange/markets/active"
LIMITLESS_SOURCE_NAME: str = "Limitless"

OPINION_SOURCE_ENABLED: bool = True
OPINION_API_URL: str = "https://openapi.opinion.trade/openapi/market"
OPINION_API_KEY: str = ""
OPINION_SOURCE_NAME: str = "Opinion"

PREDICT_FUN_SOURCE_ENABLED: bool = True
PREDICT_FUN_API_URL: str = "https://api.predict.fun/v1/markets"
PREDICT_FUN_API_KEY: str = ""
PREDICT_FUN_SOURCE_NAME: str = "Predict.fun"
```

Credential-gated sources must treat empty keys as intentionally disabled, not as errors.

## Discovery behavior

The discovery pipeline should include new sources as extra candidate producers, not as replacements:

- Keep Polymarket and Kalshi active.
- Keep Metaculus, World Cup, and Open Web behavior unchanged.
- Add Limitless, Opinion, and Predict.fun to candidate collection with source-isolated error handling.
- Do not add Probable.
- Do not reintroduce Manifold.

The no-candidates operator message should mention the active/configurable sources without implying Probable is active.

## Deduplication and source priority

Add source weights only for adapters that are actually live:

- Polymarket remains highest among active market sources.
- Kalshi remains active.
- Limitless, Opinion, and Predict.fun can be lower-priority than Polymarket/Kalshi until reliability is observed.
- Probable must not receive a source weight in Phase 2.
- Manifold must remain absent from active source weights.

Exact priority ordering should be conservative:

```python
Polymarket > Kalshi > Limitless > Opinion > Predict.fun
```

This protects existing behavior if duplicate questions appear across sources.

## Frontend impact

The existing Phase 1 platform list already shows all four requested platforms. Phase 2 should update only status semantics:

- Limitless, Opinion, and Predict.fun become active only if their backend discovery adapter is enabled/configured.
- Probable remains planned.
- Chain labels stay visible.
- Historical Manifold display remains supported for existing event records, but Manifold stays absent from platform search.

If backend does not expose runtime source status yet, frontend can keep static labels for this phase; do not create a large source-status API unless the implementation plan finds an existing suitable endpoint.

## Out of scope

Do not implement:

- Probable adapter without verified official interface.
- Auto-resolution for Limitless, Opinion, Predict.fun, or Probable.
- Trading, wallet, signing, or order placement.
- Contract-event indexing unless official docs require it and endpoint details are verified.
- Homepage scraping or reverse-engineering private APIs.
- Synthetic markets or fabricated baseline probabilities.

## Testing strategy

Use TDD for every adapter.

Backend tests:

- Each adapter:
  - returns `[]` when disabled or missing credentials;
  - fetches the documented endpoint with correct headers;
  - normalizes at least one representative market payload into the candidate-event contract;
  - skips malformed or unsupported markets;
  - fails closed on HTTP/network errors.
- Discovery:
  - calls Limitless when enabled;
  - calls Opinion only when key exists;
  - calls Predict.fun only when key exists;
  - does not call Probable;
  - does not call Manifold.
- Dedup/config:
  - source weights include only active implemented sources;
  - Probable and Manifold are absent from active weights.

Frontend tests:

- Market links continue to show the four platforms.
- Limitless, Opinion, and Predict.fun status text reflects active/planned semantics if the implementation changes static labels.
- Probable remains planned.

Verification commands:

```powershell
cd backend
python -m unittest tests.test_limitless_event_source tests.test_opinion_event_source tests.test_predict_fun_event_source tests.test_event_intelligence_service tests.test_candidate_dedup_service tests.test_config_defaults
python -m compileall app scripts

cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```

## Rollout

1. Add adapter tests and adapter modules one source at a time.
2. Add configuration and discovery integration after adapter tests pass.
3. Keep credential-gated sources disabled unless keys are configured.
4. Update docs and memory.
5. Run final verification.

## Risks and mitigations

- API schemas may differ from documentation examples.
  - Mitigation: normalize defensively; skip ambiguous records; no fabricated values.
- Opinion/Predict.fun keys may not be available locally.
  - Mitigation: tests mock HTTP and verify missing-key fail-closed behavior.
- A new source could flood duplicates.
  - Mitigation: conservative source priority and existing dedupe path.
- Probable pressure to “just add it.”
  - Mitigation: explicit planned-only status until official interface is verified.

## Self-review

- No source adapter is proposed for Probable.
- No auto-resolution, trading, or private API use is included.
- All live adapters are tied to verified official interfaces.
- Credential-gated adapters fail closed when keys are absent.
- Existing Polymarket/Kalshi/Metaculus/World Cup/Open Web behavior remains in scope to preserve.

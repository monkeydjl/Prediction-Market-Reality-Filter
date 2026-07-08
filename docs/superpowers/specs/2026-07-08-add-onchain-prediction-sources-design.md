# Add On-Chain Prediction Sources Design

Date: 2026-07-08

## Goal

Add support for four additional prediction-market sources without prematurely
claiming live discovery integration:

- Opinion — BNB Chain
- Predict.fun — BNB Chain
- Probable — BNB Chain
- Limitless — Base Chain

The first implementation phase creates a stable source registry and frontend
visibility. A later phase will add real candidate-fetch adapters after verifying
each platform's official API, indexer, subgraph, or contract event surface.

## Non-goals

- Do not scrape or reverse-engineer unofficial endpoints in this phase.
- Do not add these four sources to active discovery until their fetch adapters
  have source-specific tests.
- Do not fabricate markets or baseline probabilities.
- Do not remove Polymarket, Kalshi, Metaculus, World Cup, or Open Web support.
- Do not re-enable Manifold as an active source.

## Recommended Approach

Use a two-phase integration.

### Phase 1: Source registry and frontend visibility

Create a canonical prediction-market platform registry that describes current
and planned platforms:

- display name;
- platform key;
- chain label;
- homepage URL;
- search URL builder when a public search URL exists;
- active discovery status;
- short status note for planned sources.

The four new platforms should be present in the registry and shown in frontend
market-search surfaces, but marked as not yet active discovery sources until
real adapters exist.

This makes the product visibly aware of the requested sources while preventing
the backend from silently producing empty or misleading discovery runs.

### Phase 2: Real source adapters

Add one adapter per source only after verifying the platform's current primary
data interface:

- official REST or GraphQL API;
- official subgraph/indexer;
- documented smart-contract events plus a reliable RPC/indexing strategy.

Each adapter must normalize into the existing candidate-event shape:

```python
{
    "question": str,
    "baseline_probability": float,
    "volume": float,
    "liquidity": float,
    "source": {
        "type": "prediction_market",
        "platform": "Opinion",
        "chain": "BNB",
        "source_id": str,
        "question": str,
        "baseline_probability": float,
        "liquidity": float,
        "volume": float,
        "url": str,
    },
}
```

Adapters fail closed: network failures, missing configuration, malformed
payloads, or unavailable platform interfaces return an empty list and log a
bounded warning. A failed new source must never block discovery from existing
sources.

## Backend Design

### Platform registry

Add a small backend module, for example:

```text
backend/app/services/prediction_market_registry.py
```

It should expose a pure function such as:

```python
def list_prediction_market_platforms() -> list[dict[str, Any]]:
    ...
```

Initial records:

| key | name | chain | active_discovery |
| --- | --- | --- | --- |
| polymarket | Polymarket | Polygon | true |
| kalshi | Kalshi | Off-chain | true |
| opinion | Opinion | BNB Chain | false |
| limitless | Limitless | Base | false |
| predict_fun | Predict.fun | BNB Chain | false |
| probable | Probable | BNB Chain | false |

The registry may include Metaculus/Open Web separately if the UI wants a wider
source catalogue, but the market-search UI only needs prediction-market style
platforms.

### Discovery

Do not add the four new platforms to `_collect_candidate_events()` during
Phase 1.

When Phase 2 adapters are implemented, each source should be added behind an
explicit config flag, for example:

```python
OPINION_SOURCE_ENABLED
LIMITLESS_SOURCE_ENABLED
PREDICT_FUN_SOURCE_ENABLED
PROBABLE_SOURCE_ENABLED
```

Default should be false until the adapter has been verified against live or
recorded official payloads. Once verified, defaults can be revisited per source.

### Source weights and deduplication

Phase 1 should not add weights because the sources are not active discovery
sources yet.

Phase 2 should add weights only for adapters that are active. Candidate
deduplication priority should treat verified market sources as structured market
sources. If no platform-specific quality evidence exists, give these sources the
same priority tier as Kalshi unless tests justify a different order.

### Auto-resolution

Do not add auto-resolution for the four new sources in Phase 1.

In Phase 2, auto-resolution requires a source-specific settled-market interface
or contract outcome index. Do not rely on text matching alone without a verified
contract/link path if the platform exposes stable market IDs.

## Frontend Design

### Shared frontend platform list

Add a small frontend module, for example:

```text
frontend/src/lib/prediction-market-platforms.ts
```

It should export a typed list used by market-link surfaces:

```ts
export interface PredictionMarketPlatform {
  key: string;
  name: string;
  chain: string;
  colorClass: string;
  homepageUrl: string;
  searchUrl?: (question: string) => string;
  activeDiscovery: boolean;
}
```

### Detail market links

Update `frontend/src/components/detail/market-links.tsx` to render platform
links from the shared list instead of hard-coding Polymarket and Kalshi.

For the four new sources:

- show their name and chain label;
- link to search URL if a stable public search URL is known;
- otherwise link to the homepage;
- do not imply that discovery is active unless `activeDiscovery` is true.

Historical or future records with `source.platform` equal to any of these names
should continue to display the platform name in the detail header and source
market link.

## Documentation Design

Update current user/developer docs to distinguish:

- active discovery sources: Polymarket, Kalshi, optional existing sources;
- planned/on-chain platforms visible in the UI: Opinion, Limitless,
  Predict.fun, Probable;
- adapter status: pending official interface verification.

Do not describe the four new platforms as active discovery sources until Phase 2
adapters are implemented and tested.

## Testing Strategy

### Phase 1 tests

Backend:

- registry returns all four new platforms with correct chain labels;
- registry marks all four as `active_discovery = false`;
- active discovery source collection does not call nonexistent adapters.

Frontend:

- market-link panel renders Opinion, Limitless, Predict.fun, and Probable;
- Polymarket and Kalshi still render;
- Manifold still does not render as an active platform search entry;
- chain labels are visible for the four new on-chain sources.

### Phase 2 tests

For each source adapter:

- fixture/raw payload normalizes to the canonical candidate-event shape;
- missing or malformed payload returns an empty list;
- source fields include platform, chain, source_id, question, URL, baseline,
  volume, and liquidity when available;
- discovery includes the source only when its enable flag is true.

Auto-resolution tests should be added only when a source has a verified
settled-market interface.

## Open Questions for Phase 2

These are deliberately left for the adapter implementation phase because they
require up-to-date platform verification:

- Does each platform expose an official market list API?
- Are market probabilities directly exposed, or must they be derived from AMM
  reserves/order books?
- What is the stable market identifier?
- Is there a public settled-market/outcome endpoint?
- Are there rate limits, API keys, or RPC/indexer dependencies?

## Rollout Notes

- Phase 1 is UI/metadata only and should be safe to ship without network access.
- Operators should not expect these four sources to contribute new events until
  Phase 2 adapters are implemented.
- The registry should make this status explicit so the frontend can avoid
  misleading labels.

## Risks

- Public URLs/search routes may differ by platform. If a stable search URL is
  not verified, use the homepage in Phase 1 rather than guessing.
- On-chain platforms may require indexing contract events; direct RPC scanning
  is too slow for discovery without a bounded indexer strategy.
- Adding these platforms to discovery before adapter verification would create
  false confidence and noisy empty-source status.

## Spec Self-Review

- Placeholder scan: no TBD/TODO placeholders remain.
- Scope check: Phase 1 is a small, testable source-registry/frontend task;
  Phase 2 is intentionally deferred per-platform adapter work.
- Ambiguity check: the spec explicitly says the four sources are visible but not
  active discovery sources in Phase 1.
- Consistency check: Manifold remains inactive and is not reintroduced as a
  platform search entry.

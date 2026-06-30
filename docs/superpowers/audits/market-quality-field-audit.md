# Market Quality Field Audit (Phase 2 Pre-Implementation)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-30-decision-quality-engine-design.md` (Phase 2 acceptance #5)
**Status:** Complete

## Summary

Audits the actual availability of `bid`, `ask`, `spread`, `volume`, `liquidity`,
and `last_updated` across the three market adapters. Findings drive the
`market_quality_service` graceful-degradation design.

## Field Availability Matrix

| Field | Candidate dict key | Polymarket | Kalshi | Metaculus |
| --- | --- | --- | --- | --- |
| `baseline` | `baseline_probability` | yes (yes_price*100) | yes (last_price*100, fallback bid/ask midpoint) | yes (community_prediction.probability_yes*100) |
| `bid_ask.bid` | `bid_ask.bid` | **missing** | yes (yes_bid*100, only when last_price=0) | **missing** |
| `bid_ask.ask` | `bid_ask.ask` | **missing** | yes (yes_ask*100, only when last_price=0) | **missing** |
| `bid_ask.spread` | `bid_ask.spread` | **missing** | yes (round(ask-bid, 2), 0.0 when bid/ask unavailable) | **missing** |
| `volume` | `volume` | yes (market.volume) | yes (market.volume_fp) | yes (**forecaster count**, not trading volume) |
| `liquidity` | `liquidity` | yes (market.liquidity) | yes (market.liquidity_dollars) | **hardcoded 0.0** |
| `last_updated` | (not in candidate dict) | **missing** | **missing** (close_time is in `source` sub-dict, not top-level) | **missing** |

## Source Type Classification

| Adapter | `source.type` | Produces `market_quality`? |
| --- | --- | --- |
| Polymarket | `prediction_market` | yes |
| Kalshi | `prediction_market` | yes |
| Metaculus | `prediction_question` | **no** (excluded per spec Applicability) |
| Manual (`analyze_event_question`) | `manual` | **no** |

## Detailed Findings

### 1. Polymarket (`polymarket_event_source.py`)

- Builder: `_to_candidate_event(market)` (line 81-105)
- Populated: `baseline_probability`, `volume`, `liquidity`, `source`
- Missing: `bid_ask` (key absent), `last_updated`
- `source` sub-dict carries: `platform`, `source_id`, `question`, `baseline_probability`, `liquidity`, `volume`, `url`, `closed`, `end_date`

### 2. Kalshi (`kalshi_event_source.py`)

- Builder: `_to_candidate_event(event)` (line 121-156)
- Populated: `baseline_probability`, `volume`, `liquidity`, `bid_ask`, `source`
- `bid_ask` structure: `{"bid": float, "ask": float, "spread": float}` (all 0-100 scale)
- **Important caveat**: bid/ask are only non-zero when `last_price_dollars == 0`.
  When last_price is available, `_baseline_and_quote` returns
  `(last*100, 0.0, 0.0)` — bid/ask/spread are placeholders, not real quotes.
- Missing: `last_updated` (but `close_time` and `status` live in `source` sub-dict)
- `source` sub-dict carries: `platform`, `source_id`, `question`, `baseline_probability`, `liquidity`, `volume`, `url`, `status`, `close_time`

### 3. Metaculus (`metacus_event_source.py`)

- Note: filename is `metacus_event_source.py` (project convention, not a typo)
- Builder: `_to_candidate_event(post)` (line 130-153)
- Populated: `baseline_probability`, `volume`, `liquidity`, `source`
- `volume` semantic: **forecaster count** (`post.user_counts.forecasters`), NOT trading volume
- `liquidity`: hardcoded `0.0` (Metaculus has no liquidity concept)
- Missing: `bid_ask`, `last_updated`
- Excluded from `market_quality` per spec (source.type = `prediction_question`)

## `market_quote` Pass-Through

`market_quote` is not produced by adapters directly. It is extracted from the
candidate dict's `bid_ask` key and passed to `analyze_event`:

```python
# event_intelligence_service.py line 673
market_quote = candidate.get("bid_ask")
```

- **Kalshi**: `record["market_quote"]` = `{"bid", "ask", "spread"}` (may be all 0.0)
- **Polymarket / Metaculus / Manual**: `market_quote` is `None`, `record["market_quote"]` key is **never created** (guarded by `if market_quote is not None`)

## `analyze_event` Field Consumption

| `analyze_event` param | Source | Available to `market_quality_service`? |
| --- | --- | --- |
| `baseline_probability` | candidate dict | yes (always) |
| `volume` | candidate dict | yes (Polymarket/Kalshi: trading volume; Metaculus: forecaster count — excluded anyway) |
| `liquidity` | candidate dict | yes (Polymarket/Kalshi: real; Metaculus: 0.0 — excluded anyway) |
| `market_quote` | candidate dict's `bid_ask` | yes for Kalshi; `None` for Polymarket/Metaculus/Manual |
| `source` | candidate dict | yes (carries `source.type` for gating) |
| `last_updated` | **not passed** | no — would require adapter + signature changes |

## Implications for `market_quality_service`

1. **`last_updated` is unavailable across all adapters.** Phase 2 `stale_price_flag`
   will always be `unknown` (not `true`/`false`). The stale-check rule is a no-op
   until adapters expose timestamps. Do NOT block Phase 2 on this — record the
   sub-score as `unknown` and skip the stale downgrade.

2. **`bid_ask` is only available for Kalshi, and even then is often 0,0,0.**
   When `market_quote` is `None` or all-zero, `spread_penalty` is `unknown`
   and `thin_market_flag` cannot be set from spread alone — fall back to
   liquidity/volume signals.

3. **Metaculus is excluded from `market_quality` entirely** (source.type =
   `prediction_question`). Its `liquidity=0.0` will NOT spuriously trigger
   `thin_market_flag` because the block is never built for Metaculus events.

4. **Manual path (`source.type = "manual"`) also produces no `market_quality`.**
   The block is only built for `prediction_market` sources.

5. **Polymarket has `volume` and `liquidity` but no `bid_ask`.** The service
   must handle missing `market_quote` gracefully — spread sub-score is
   `unknown`, but liquidity/volume sub-scores can still fire `thin_market_flag`.

## Phase 2 Design Decisions (Based on Audit)

- `spread_penalty`: 0.0 when `market_quote` is `None` or all-zero; computed
  from `spread / 100` when real bid/ask exist (Kalshi only, when last_price=0)
- `thin_market_flag`: `true` when `liquidity < MARKET_MIN_LIQUIDITY` OR
  `volume < MARKET_MIN_VOLUME` (independent of spread)
- `stale_price_flag`: always `unknown` in Phase 2 (no `last_updated`)
- `score`: weighted average of available sub-scores; missing sub-scores
  recorded as `unknown` and excluded from the average
- Downgrade: `suggested_direction = WAIT` when `score < threshold` AND
  `raw_direction` is YES/NO (strong direction)

## Verification

This audit was completed BEFORE Phase 2 implementation, per spec acceptance
criterion #5. Findings are read-only — no adapter files were modified.

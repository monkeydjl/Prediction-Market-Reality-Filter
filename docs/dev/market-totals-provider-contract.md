# Market over/under totals-line provider contract

## Purpose

`backend/app/services/market_totals_service.py` optionally replaces the league-average placeholder line in the soft totals diagnostic with a real posted over/under. It is a read-only, process-cached data source that fills `custom.market_total_line` (and `custom.market_total_p_over`); it does not write to the kernel database, create predictions, or change any engine formula or weight.

The placeholder it replaces is not merely approximate — for basketball, baseball, and hockey it makes the diagnostic vacuous. Those engines derive scores as `league_avg/2 ± margin/2`, so `home + away == league_avg` exactly, and the soft-totals call is passed `line=league_avg`. The line therefore equals the expected total by construction and `p_over` is a **per-sport constant**: measured at exactly `0.4821` for basketball at margins of 0, +5, +15, and −12 alike. Football differs only because `_probabilities_to_scores` applies a draw factor, so its expected total does move against its fixed 2.5 line.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or the URL, key, or either parameter name is blank, it makes no outbound request:

```dotenv
MARKET_TOTALS_ENABLED=true
MARKET_TOTALS_URL=https://provider.example/odds/totals
MARKET_TOTALS_API_KEY=...
MARKET_TOTALS_SPORT_PARAM=sport
MARKET_TOTALS_DATE_PARAM=date
```

The sport code and match date are appended as the configured query parameters, replacing any values already present in the URL — other query parameters are kept. The sport is lowercased; the date is re-emitted canonically. The two parameter names must differ, since one parameter cannot carry both values. Requests use `Authorization: Bearer <key>` and are bounded by `MARKET_TOTALS_TIMEOUT_S` and `MARKET_TOTALS_MAX_BYTES`. Snapshots are cached per resolved URL for `MARKET_TOTALS_CACHE_TTL_MINUTES`, so each sport, each date, and any configuration change gets its own entry. Credentials and raw responses are never logged, returned by diagnostics, or exposed via an API. Only `http` and `https` URLs are accepted.

### The date shape is required exactly

The request date must be exactly `YYYY-MM-DD`. It is validated with `strptime`, not parsed loosely, and then re-emitted in canonical form. A provider handed a timestamp or a partial date is free to ignore it and return some other day's board, and a wrong-day snapshot would look perfectly valid while quoting lines for the wrong fixtures. Single-digit month/day input (`2026-8-19`) is accepted and canonicalized before the request; an ISO timestamp, `20260819`, or an impossible date is rejected before any request is made.

## Required response envelope

```json
{
  "games": [
    {
      "home": "Boston Celtics",
      "away": "Miami Heat",
      "total_line": 228.5,
      "over_odds": 1.91,
      "under_odds": 1.95
    },
    {
      "home": "Denver Nuggets",
      "away": "Phoenix Suns",
      "total_line": null,
      "over_odds": null,
      "under_odds": null
    }
  ]
}
```

Rules:

- The payload must be a JSON object with a `games` list and no `errors` value.
- Every entry must be an object with non-empty `home` and `away` names. Names are matched case-, accent-, and punctuation-insensitively. The pair is directional: a fixture listed with the sides swapped is a **different** fixture, not the same one, because the home side determines which team is being quoted at home. A self-paired row, or the same pair listed twice, makes the snapshot ambiguous and rejects it.
- **Both prices are required.** `over_odds` and `under_odds` must both be present as keys. This is the structural-integrity requirement of this provider: a line on its own is a number with nothing behind it, and a two-sided quote is what makes it a market. A payload carrying only `total_line` is rejected, exactly as the MLB park provider rejects a pre-computed factor with no game counts behind it.
- Prices are decimal odds strictly above `1.0`. The implied probabilities are de-vigged locally as `p_over = (1/over_odds) / (1/over_odds + 1/under_odds)`; the raw sum (the overround) must sit in `(1.00, 1.30]`. No overround at all means the pair is not a book's prices; an implausibly large one means it is not a market.
- The de-vigged over probability must be within `MARKET_TOTALS_MAX_PRICE_SKEW` (default `0.15`) of even. A book's posted total is by definition the level it has balanced, so a heavily skewed price means the number is not that level and the row cannot be trusted. This check bounds the price, **not** the model: the de-vigged probability itself is published verbatim and is not forced to `0.5`.
- The line must fall inside `[0.5×, 2.0×]` of the baseline it replaces (`NBA_LEAGUE_AVG_TOTAL`, `MLB_LEAGUE_AVG_TOTAL`, `NHL_LEAGUE_AVG_TOTAL`, or football's 2.5). The band is wide enough for any real book line and narrow enough to catch a cross-sport unit error: a basketball total of 5.5 (a spread mistaken for a total) or a football total of 220 both fall outside it. An unknown sport code is rejected rather than banded against a guess.
- Malformed JSON, a non-UTF-8 body, an oversized response, a transport error, a timeout, or an unreadable configuration value invalidates the entire snapshot.

### Unpriced markets versus malformed data

These are treated differently on purpose:

- A **structurally broken** row (non-object, missing/duplicate/self-paired teams, one price supplied without the other, non-numeric or non-finite values, odds at or below 1.0, an overround outside the window, an over-skewed price, a line outside the unit band) rejects the **whole snapshot**. The contract is either honoured or it is not.
- A row whose `over_odds` and `under_odds` are **both explicitly `null`** is a real fixture whose market is suspended or not yet open. Only **that fixture** is stored empty; every other row in the snapshot stays usable and the baseline covers the unpriced one.

`available=True, total=None` therefore means the provider was reached but carries no usable quote for this fixture — it either did not list it or published it unpriced. That is deliberately distinct from `available=False`, and neither is an assertion that the market is even.

## Fallback behavior

The line reaches the engines through `inject_market_total_into_custom`, called from five adapter paths: `mlb_adapter.fetch_all_data`, `nhl_adapter.fetch_all_data`, `nba_adapter.fetch_all_data`, the football composition root `adapters/_shared.fetch_elo_and_odds` (covering the EPL, UCL, and generic league adapters), and `world_cup_adapter._build_custom`, which builds `custom` itself and would otherwise silently keep the 2.5 placeholder. The helper never raises, never overwrites a line a caller already set, and returns `custom` untouched when the provider is disabled, unreachable, unpriced, or throws.

`soft_totals_btts["line_source"]` records provenance for diagnostics only:

- `market_provider` — the line came from this provider, and `market_p_over` carries the de-vigged book probability alongside the model's own `p_over`;
- `league_average` — the line is the sport's league-average baseline (or football's 2.5), and `market_p_over` is absent.

A malformed value in `custom` degrades silently and totally: `resolve_totals_line` falls back to the baseline rather than quoting against a nonsense line. A usable line with an unusable companion probability keeps the line and drops the probability, since the line is the substantive datum.

## The expected total is still model-derived — read the comparison accordingly

**This is the most important caveat in this document.** The line is a market datum while the expected total remains model-derived, so the pair is deliberately mixed-source. The divergence is the signal — but for basketball, baseball, and hockey the model side of that comparison is still the **league average**, not a team-specific scoring forecast. With this provider enabled, `p_over` for those sports reflects how far the book's line sits from the league average, not how much these two teams are expected to score. A team-specific expected total needs a scoring model those engines do not have; adding one would change engine formulas, which are frozen for this track.

Football's expected total does respond to the fixture, via the draw factor applied in `_probabilities_to_scores`, so its comparison is the least confounded of the four.

The frontend panel states this in place rather than leaving it implicit: it badges the line as `真实盘口线` or `联赛均值线`, and when the line and the expected total coincide it renders a caveat saying the over/under split carries no fixture-specific information. That condition is checked numerically (`|expected − line| < 0.05`) rather than by sport, so football's fixed 2.5 placeholder is not mislabelled when the two happen to differ.

## Not covered by this provider

The P1-O1 backlog row also names **亚盘** (Asian handicap / spread markets), which is **not** delivered here and stays open. A spread line needs a margin distribution — a Skellam or equivalent difference model — which none of these engines expose, plus a frontend consumer that does not exist. Publishing a handicap line with no margin model behind it would be a number with nothing to compare it against, which is the same defect this provider was written to close.

Nothing here delivers a full multi-market book (money line, correct score, half-time lines, player props). The soft totals diagnostic remains an independent-Poisson estimate over a single total.

## Operational readiness

Keep the feature disabled until a licensed source can publish, per fixture, the posted total together with both decimal prices for the requested day. Enabling this provider does not enable learning, scheduling, market writes, or prediction writes.

# NHL true 5v5 shot-quality provider contract

## Purpose

`backend/app/services/nhl_live_xg_service.py` optionally replaces the club-stats shot proxies with measured 5v5 expected goals and corsi. It is a read-only, process-cached data source that fills the existing `custom.xg_for_{home,away}` / `custom.corsi_pct_{home,away}` fields; it does not write to the kernel database, create predictions, or change `HockeyEngine`'s `attack_share` formula or weight.

The official NHL club-stats feed carries no team-level xG or corsi. `backend/app/sports/hockey/nhl_stats_client.py` therefore derives soft stand-ins — shots on goal scaled by `0.09` as an xG-like level, and the shots-on-goal share as a corsi-like possession proxy — and those remain the fallback.

## Configuration

Set every required value in `backend/.env`. If the feature is disabled or either URL/key is blank, it makes no outbound request:

```dotenv
NHL_LIVE_XG_ENABLED=true
NHL_LIVE_XG_URL=https://provider.example/nhl/5v5
NHL_LIVE_XG_API_KEY=...
NHL_LIVE_XG_SEASON_PARAM=season
```

The season start year is appended as the configured query parameter, replacing any value already present in the URL — season key `20262027` becomes `season=2026`. Requests use `Authorization: Bearer <key>` and are bounded by `NHL_LIVE_XG_TIMEOUT_S` and `NHL_LIVE_XG_MAX_BYTES`. Snapshots are cached per resolved URL for `NHL_LIVE_XG_CACHE_TTL_HOURS`, so each season and any configuration change gets its own entry. Credentials and raw responses are never logged, returned by diagnostics, or exposed via an API. Only `http` and `https` URLs are accepted.

## Required response envelope

```json
{
  "teams": [
    {
      "team": "Boston Bruins",
      "toi_minutes": 1000.0,
      "xgf": 40.0,
      "cf": 1100.0,
      "ca": 900.0
    }
  ]
}
```

Rules:

- The payload must be a JSON object with a `teams` list and no `errors` value.
- Every entry must be an object with a non-empty `team`. Team names are matched case- and punctuation-insensitively, and every normalized name must be unique — duplicate blocks make the snapshot ambiguous and reject it.
- **`toi_minutes` is always required** and must be finite and positive. It is the 5v5 ice-time sample behind both metrics.
- **Each metric group is optional but must arrive complete.** Expected goals needs `xgf` (finite, non-negative). Corsi needs both `cf` and `ca` (finite, non-negative, positive sum). A half-supplied corsi pair is a contract violation, and a row carrying neither group has nothing measurable in it — both reject the snapshot.
- **The rates are computed here** as `60 × xgf / toi_minutes` and `cf / (cf + ca)`. A payload that carries only pre-computed `xgf_per_60`/`corsi_pct` is rejected: without ice time and event counts it cannot be shown to be a measured 5v5 rate, which is exactly the gap this provider exists to close. Goals, shots on goal, scoring chances, and estimated rates are not valid substitutes for expected goals.
- A computed `xgf_per_60` outside `[1.0, 4.5]` rejects the whole snapshot — a value beyond that band means the payload is not in xG-per-60 at all, so the entire feed's units are untrustworthy. A computed corsi share outside `[0.30, 0.70]` likewise rejects it: the feed is counting something other than shot attempts for and against.
- Malformed JSON, a non-UTF-8 body, an oversized response, a transport error, a timeout, or an unreadable configuration value invalidates the entire snapshot.

### Sample size versus malformed data

These are treated differently on purpose:

- A **structurally broken** row (non-object, missing/duplicate team, non-numeric or non-finite field, non-positive ice time, negative counts, half-supplied corsi pair, no metric group at all, out-of-band rate) rejects the **whole snapshot**. The contract is either honoured or it is not.
- A well-formed row with **fewer than `NHL_LIVE_XG_MIN_TOI_MINUTES`** of 5v5 ice time is real data with an unusable sample. Only **that team** is dropped, and the club-stats proxies cover it. The default of 500 minutes is roughly ten games of 5v5 play.

A valid snapshot that omits the requested team, or reports it with too small a sample, is *available data with no usable measurement* — not an assertion that the team is league-average.

## Fallback behavior

`backend/app/sports/hockey/nhl_adapter.py` applies the pair rule **per metric**: a metric is written only when **both** sides carry it live.

1. both sides have live corsi → `corsi_pct_{home,away}` are the measured 5v5 shares;
2. both sides have live expected goals → `xg_for_{home,away}` are the measured xGF/60 rates;
3. neither pair is complete → both proxies stay exactly as the club-stats path produced them.

Mixing within one metric is deliberately impossible. `HockeyEngine` consumes a home-vs-away share, so pairing a measured 5v5 rate against a shots-on-goal proxy would manufacture a spurious edge from nothing but the source difference. One live side is therefore not enough. Mixing *across* metrics is harmless because the engine picks a single branch rather than combining them.

One consequence needs stating: `HockeyEngine` prefers corsi over expected goals. When the provider supplies measured xG for both sides but no corsi, the adapter clears `corsi_pct_{home,away}` so the shots-on-goal proxy cannot shadow real data. That changes which branch of the existing factor fires; it does not change the formula, the coefficients, or the weight.

`custom.skating_source` records provenance for diagnostics only:

- `live_provider` — at least one measured pair was written;
- `club_stats_proxy` — both sides came from club-stats rates;
- `soft_form` — at least one side had no club-stats rates and is form-shaped.

A disabled or unconfigured provider, a transport failure, a rejected snapshot, an exception inside the service, or a provider that lacks a usable metric for either side all degrade to the club-stats path.

## Operational readiness

Keep the feature disabled until a licensed source can publish 5v5 ice time together with actual expected goals and/or actual corsi event counts for the intended seasons. Enabling this provider does not enable learning, scheduling, market writes, or prediction writes.

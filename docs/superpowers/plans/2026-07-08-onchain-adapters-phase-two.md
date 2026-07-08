# On-Chain Prediction Source Adapters Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live candidate-discovery adapters for Limitless, Opinion, and Predict.fun, while keeping Probable planned-only until an official interface is verified.

**Architecture:** Add three thin `*_event_source.py` adapters that follow the existing Kalshi/Metaculus source pattern and emit the shared candidate-event shape. Limitless is public and default-enabled; Opinion and Predict.fun are API-key gated and return `[]` without network calls when keys are missing. Discovery integrates only verified adapters; Probable and Manifold stay absent from active discovery and auto-resolution.

**Tech Stack:** Python `unittest`, `httpx.AsyncClient`, FastAPI service modules, TypeScript React, Vitest/Testing Library.

## Global Constraints

- Implement live candidate discovery only for Limitless, Opinion, and Predict.fun.
- Do not implement a Probable adapter until an official API, indexer, or contract-event interface is verified.
- Do not scrape homepages, infer private APIs, or fabricate markets.
- Do not add auto-resolution for Limitless, Opinion, Predict.fun, or Probable.
- Do not add trading, wallet, signing, or order-placement behavior.
- Do not reintroduce Manifold as an active source or frontend platform search entry.
- Keep Polymarket and Kalshi active.
- Keep Metaculus, World Cup, and Open Web behavior unchanged.
- Use TDD: write failing tests before implementation changes.
- Credential-gated sources must treat empty keys as intentionally disabled and return `[]` without network calls.
- All adapters must fail closed by returning `[]` on HTTP errors, malformed payloads, or unsupported market shapes.
- Candidate events must use `source.type == "prediction_market"` and include `source.chain` for the new on-chain sources.

---

## File Structure

- Create `backend/app/services/limitless_event_source.py` and `backend/tests/test_limitless_event_source.py`.
- Create `backend/app/services/opinion_event_source.py` and `backend/tests/test_opinion_event_source.py`.
- Create `backend/app/services/predict_fun_event_source.py` and `backend/tests/test_predict_fun_event_source.py`.
- Modify `backend/app/core/config.py` and `backend/tests/test_config_defaults.py`.
- Modify `backend/app/services/event_intelligence_service.py` and `backend/tests/test_event_intelligence_service.py`.
- Modify `backend/app/services/candidate_dedup_service.py` and `backend/tests/test_candidate_dedup_service.py`.
- Modify `backend/app/services/prediction_market_registry.py` and `backend/tests/test_prediction_market_registry.py`.
- Modify `frontend/src/lib/prediction-market-platforms.ts`, `frontend/src/components/detail/market-links.tsx`, and `frontend/src/components/detail/market-links.test.tsx`.
- Modify current docs: `README.md`, `docs/user/USER_GUIDE.md`, `docs/user/QUICK_START.md`, `docs/dev/ARCHITECTURE.md`.
- Append `SESSION_MEMORY_2026-07-08.md`.

---

### Task 1: Limitless public adapter

**Files:**
- Create: `backend/app/services/limitless_event_source.py`
- Create: `backend/tests/test_limitless_event_source.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_config_defaults.py`

**Interfaces:**
- Produces: `fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]`
- Consumes: `settings.LIMITLESS_SOURCE_ENABLED`, `settings.LIMITLESS_API_URL`, `settings.LIMITLESS_SOURCE_NAME`

- [ ] **Step 1: Write failing config tests**

Append to `backend/tests/test_config_defaults.py`:

```python
class OnChainSourceConfigDefaultsTests(unittest.TestCase):
    def test_limitless_defaults_to_public_active_endpoint(self):
        self.assertTrue(settings.LIMITLESS_SOURCE_ENABLED)
        self.assertEqual(settings.LIMITLESS_API_URL, "https://api.limitless.exchange/markets/active")
        self.assertEqual(settings.LIMITLESS_SOURCE_NAME, "Limitless")

    def test_onchain_source_weights_exclude_probable_and_manifold(self):
        self.assertIn("Limitless", settings.SOURCE_WEIGHTS)
        self.assertNotIn("Probable", settings.SOURCE_WEIGHTS)
        self.assertNotIn("Manifold", settings.SOURCE_WEIGHTS)
```

Run:

```powershell
cd backend
python -m unittest tests.test_config_defaults.OnChainSourceConfigDefaultsTests
```

Expected: FAIL with missing `LIMITLESS_*` settings or missing `Limitless` weight.

- [ ] **Step 2: Write failing Limitless adapter tests**

Create `backend/tests/test_limitless_event_source.py` with tests that:

```python
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import limitless_event_source as source


def _market(**overrides):
    market = {
        "id": "lim-1",
        "title": "Will ETH close above $5,000 in 2026?",
        "yesProbability": 0.62,
        "volume": 1200.5,
        "liquidity": 450.25,
        "url": "https://limitless.exchange/markets/lim-1",
        "status": "active",
    }
    market.update(overrides)
    return market


class LimitlessEventSourceTests(unittest.TestCase):
    def test_fetch_candidate_events_normalizes_market(self):
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])):
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events[0]["question"], "Will ETH close above $5,000 in 2026?")
        self.assertEqual(events[0]["baseline_probability"], 62.0)
        self.assertEqual(events[0]["source"]["platform"], "Limitless")
        self.assertEqual(events[0]["source"]["chain"], "Base")

    def test_filters_malformed_closed_and_ambiguous_markets(self):
        raw = [
            _market(id="ok"),
            _market(id="closed", status="closed"),
            _market(id="blank", title="   "),
            _market(id="missing-probability", yesProbability=None),
            "not a dict",
        ]
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=raw)):
            events = asyncio.run(source.fetch_candidate_events(limit=10))
        self.assertEqual([e["source"]["source_id"] for e in events], ["ok"])

    def test_disabled_or_empty_url_returns_empty_without_fetching(self):
        with patch.object(source.settings, "LIMITLESS_SOURCE_ENABLED", False), \
                patch.object(source, "_fetch_raw_markets", new=AsyncMock(return_value=[_market()])) as fetch:
            self.assertEqual(asyncio.run(source.fetch_candidate_events(limit=5)), [])
            fetch.assert_not_called()

    def test_fetch_error_degrades_to_empty(self):
        with patch.object(source, "_fetch_raw_markets", new=AsyncMock(side_effect=RuntimeError("boom"))), \
                self.assertLogs("app.services.limitless_event_source", level="WARNING") as logs:
            events = asyncio.run(source.fetch_candidate_events(limit=5))
        self.assertEqual(events, [])
        self.assertIn("source=limitless_candidates", "\n".join(logs.output))
```

Also include a small fake `httpx.AsyncClient` test asserting `_fetch_raw_markets(limit=7)` calls `settings.LIMITLESS_API_URL` with `params={"limit": "35"}` and no headers.

Run:

```powershell
cd backend
python -m unittest tests.test_limitless_event_source
```

Expected: FAIL with `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Add Limitless config**

In `backend/app/core/config.py`, near the Kalshi/Metaculus source settings, add:

```python
    LIMITLESS_SOURCE_ENABLED: bool = _env_bool("LIMITLESS_SOURCE_ENABLED", "true")
    LIMITLESS_API_URL: str = os.getenv(
        "LIMITLESS_API_URL",
        "https://api.limitless.exchange/markets/active",
    )
    LIMITLESS_SOURCE_NAME: str = os.getenv("LIMITLESS_SOURCE_NAME", "Limitless")
```

In `SOURCE_WEIGHTS`, add:

```python
        "Limitless": float(os.getenv("SOURCE_WEIGHT_LIMITLESS", "0.8")),
```

- [ ] **Step 4: Implement Limitless adapter**

Create `backend/app/services/limitless_event_source.py`. Implementation requirements:

```python
async def fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]:
    if not settings.LIMITLESS_SOURCE_ENABLED or not settings.LIMITLESS_API_URL:
        return []
    try:
        raw_markets = await _fetch_raw_markets(limit)
    except Exception as exc:
        return fail_closed_empty_list(logger, "limitless_candidates", exc, context={"limit": limit})
    return [_to_candidate_event(m) for m in raw_markets if _is_eligible(m)][:limit]
```

Required helper behavior:

- `_fetch_raw_markets(limit)`:
  - uses `httpx.AsyncClient(timeout=30)`;
  - GETs `settings.LIMITLESS_API_URL`;
  - sends `params={"limit": str(min(max(limit * 5, limit, 1), 100))}`;
  - accepts response shapes `list`, `{"markets": list}`, `{"data": list}`, or `{"results": list}`.
- `_is_eligible(market)`:
  - accepts dicts only;
  - requires non-blank question from `title`, `question`, or `name`;
  - requires probability from `yesProbability`, `yes_probability`, `probability`, `probabilityYes`, or `lastPrice`;
  - rejects statuses `closed`, `resolved`, `settled`, `finalized`, `cancelled`, `canceled`;
  - rejects `resolved is True` or `closed is True`.
- `_to_candidate_event(market)`:
  - normalizes probability: `0-1` values become `0-100`, `0-100` values are preserved, other values are skipped by `_is_eligible`;
  - extracts `volume` from `volume`, `volumeUsd`, `volume_usd`, or `totalVolume`;
  - extracts `liquidity` from `liquidity`, `liquidityUsd`, `liquidity_usd`, or `totalLiquidity`;
  - extracts ID from `id`, `marketId`, `market_id`, or `slug`;
  - emits `source.chain == "Base"`.

- [ ] **Step 5: Verify and commit Limitless**

Run:

```powershell
cd backend
python -m unittest tests.test_limitless_event_source tests.test_config_defaults.OnChainSourceConfigDefaultsTests
```

Expected: PASS.

Commit:

```powershell
git add backend/app/services/limitless_event_source.py backend/tests/test_limitless_event_source.py backend/app/core/config.py backend/tests/test_config_defaults.py
git diff --cached --name-only
git commit -m "feat: add Limitless candidate source"
```

Verify the cached file list contains only those four files.

---

### Task 2: Opinion API-key gated adapter

**Files:**
- Create: `backend/app/services/opinion_event_source.py`
- Create: `backend/tests/test_opinion_event_source.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_config_defaults.py`

**Interfaces:**
- Produces: `fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]`
- Consumes: `settings.OPINION_SOURCE_ENABLED`, `settings.OPINION_API_URL`, `settings.OPINION_API_KEY`, `settings.OPINION_SOURCE_NAME`
- Fetches with header `apikey: settings.OPINION_API_KEY`.

- [ ] **Step 1: Write failing config test**

Append to `OnChainSourceConfigDefaultsTests`:

```python
    def test_opinion_defaults_to_key_gated_endpoint(self):
        self.assertTrue(settings.OPINION_SOURCE_ENABLED)
        self.assertEqual(settings.OPINION_API_URL, "https://openapi.opinion.trade/openapi/market")
        self.assertEqual(settings.OPINION_API_KEY, "")
        self.assertEqual(settings.OPINION_SOURCE_NAME, "Opinion")
        self.assertIn("Opinion", settings.SOURCE_WEIGHTS)
```

Run:

```powershell
cd backend
python -m unittest tests.test_config_defaults.OnChainSourceConfigDefaultsTests.test_opinion_defaults_to_key_gated_endpoint
```

Expected: FAIL with missing settings or weight.

- [ ] **Step 2: Write failing Opinion adapter tests**

Create `backend/tests/test_opinion_event_source.py`. Use a representative market:

```python
def _market(**overrides):
    market = {
        "id": "op-1",
        "question": "Will BNB close above $1,000 in 2026?",
        "probability": 0.41,
        "volume": 777.0,
        "liquidity": 333.0,
        "url": "https://app.opinion.trade/market/op-1",
        "status": "open",
    }
    market.update(overrides)
    return market
```

Tests must assert:

- missing `OPINION_API_KEY` returns `[]` and does not call `_fetch_raw_markets`;
- `_fetch_raw_markets(limit=4)` sends `headers={"apikey": "secret"}` and `params={"limit": "20"}`;
- normalized output has `platform == "Opinion"` and `chain == "BNB Chain"`;
- closed, blank, missing-probability, and probability `>100` markets are skipped;
- `_fetch_raw_markets` exception returns `[]` and logs `source=opinion_candidates`.

Run:

```powershell
cd backend
python -m unittest tests.test_opinion_event_source
```

Expected: FAIL with missing module.

- [ ] **Step 3: Add Opinion config**

In `backend/app/core/config.py`, add:

```python
    OPINION_SOURCE_ENABLED: bool = _env_bool("OPINION_SOURCE_ENABLED", "true")
    OPINION_API_URL: str = os.getenv(
        "OPINION_API_URL",
        "https://openapi.opinion.trade/openapi/market",
    )
    OPINION_API_KEY: str = os.getenv("OPINION_API_KEY", "")
    OPINION_SOURCE_NAME: str = os.getenv("OPINION_SOURCE_NAME", "Opinion")
```

In `SOURCE_WEIGHTS`, add:

```python
        "Opinion": float(os.getenv("SOURCE_WEIGHT_OPINION", "0.6")),
```

- [ ] **Step 4: Implement Opinion adapter**

Create `backend/app/services/opinion_event_source.py` using the same structure as Limitless with these differences:

- `fetch_candidate_events()` returns `[]` when `OPINION_SOURCE_ENABLED` is false, `OPINION_API_URL` is empty, or `OPINION_API_KEY` is empty.
- `_fetch_raw_markets()` sends `headers={"apikey": settings.OPINION_API_KEY}`.
- Accepted response list keys are `data`, `markets`, `results`, and `list`.
- URL fallback is `https://app.opinion.trade/market/{source_id}`.
- `source.chain` is `"BNB Chain"`.
- Failure policy source name is `"opinion_candidates"`.

- [ ] **Step 5: Verify and commit Opinion**

Run:

```powershell
cd backend
python -m unittest tests.test_opinion_event_source tests.test_config_defaults.OnChainSourceConfigDefaultsTests.test_opinion_defaults_to_key_gated_endpoint
```

Expected: PASS.

Commit:

```powershell
git add backend/app/services/opinion_event_source.py backend/tests/test_opinion_event_source.py backend/app/core/config.py backend/tests/test_config_defaults.py
git diff --cached --name-only
git commit -m "feat: add Opinion candidate source"
```

---

### Task 3: Predict.fun API-key gated adapter

**Files:**
- Create: `backend/app/services/predict_fun_event_source.py`
- Create: `backend/tests/test_predict_fun_event_source.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_config_defaults.py`

**Interfaces:**
- Produces: `fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]`
- Consumes: `settings.PREDICT_FUN_SOURCE_ENABLED`, `settings.PREDICT_FUN_API_URL`, `settings.PREDICT_FUN_API_KEY`, `settings.PREDICT_FUN_SOURCE_NAME`
- Fetches with header `x-api-key: settings.PREDICT_FUN_API_KEY`.

- [ ] **Step 1: Write failing config test**

Append to `OnChainSourceConfigDefaultsTests`:

```python
    def test_predict_fun_defaults_to_key_gated_endpoint(self):
        self.assertTrue(settings.PREDICT_FUN_SOURCE_ENABLED)
        self.assertEqual(settings.PREDICT_FUN_API_URL, "https://api.predict.fun/v1/markets")
        self.assertEqual(settings.PREDICT_FUN_API_KEY, "")
        self.assertEqual(settings.PREDICT_FUN_SOURCE_NAME, "Predict.fun")
        self.assertIn("Predict.fun", settings.SOURCE_WEIGHTS)
```

Run:

```powershell
cd backend
python -m unittest tests.test_config_defaults.OnChainSourceConfigDefaultsTests.test_predict_fun_defaults_to_key_gated_endpoint
```

Expected: FAIL with missing settings or weight.

- [ ] **Step 2: Write failing Predict.fun adapter tests**

Create `backend/tests/test_predict_fun_event_source.py`. Use a representative market:

```python
def _market(**overrides):
    market = {
        "id": "pf-1",
        "title": "Will BTC close above $150,000 in 2026?",
        "probability": 58.0,
        "volume": 900.0,
        "liquidity": 150.0,
        "url": "https://predict.fun/markets/pf-1",
        "status": "active",
    }
    market.update(overrides)
    return market
```

Tests must assert:

- missing `PREDICT_FUN_API_KEY` returns `[]` and does not call `_fetch_raw_markets`;
- `_fetch_raw_markets(limit=6)` sends `headers={"x-api-key": "secret"}` and `params={"limit": "30"}`;
- normalized output has `platform == "Predict.fun"` and `chain == "BNB Chain"`;
- resolved/closed, blank, missing-probability, and negative-probability markets are skipped;
- `_fetch_raw_markets` exception returns `[]` and logs `source=predict_fun_candidates`.

Run:

```powershell
cd backend
python -m unittest tests.test_predict_fun_event_source
```

Expected: FAIL with missing module.

- [ ] **Step 3: Add Predict.fun config**

In `backend/app/core/config.py`, add:

```python
    PREDICT_FUN_SOURCE_ENABLED: bool = _env_bool("PREDICT_FUN_SOURCE_ENABLED", "true")
    PREDICT_FUN_API_URL: str = os.getenv(
        "PREDICT_FUN_API_URL",
        "https://api.predict.fun/v1/markets",
    )
    PREDICT_FUN_API_KEY: str = os.getenv("PREDICT_FUN_API_KEY", "")
    PREDICT_FUN_SOURCE_NAME: str = os.getenv("PREDICT_FUN_SOURCE_NAME", "Predict.fun")
```

In `SOURCE_WEIGHTS`, add:

```python
        "Predict.fun": float(os.getenv("SOURCE_WEIGHT_PREDICT_FUN", "0.5")),
```

- [ ] **Step 4: Implement Predict.fun adapter**

Create `backend/app/services/predict_fun_event_source.py` using the same structure as Opinion with these differences:

- `_fetch_raw_markets()` sends `headers={"x-api-key": settings.PREDICT_FUN_API_KEY}`.
- Accepted response list keys are `markets`, `data`, `results`, and `items`.
- URL fallback is `https://predict.fun/markets/{source_id}`.
- `source.chain` is `"BNB Chain"`.
- Failure policy source name is `"predict_fun_candidates"`.

- [ ] **Step 5: Verify and commit Predict.fun**

Run:

```powershell
cd backend
python -m unittest tests.test_predict_fun_event_source tests.test_config_defaults.OnChainSourceConfigDefaultsTests.test_predict_fun_defaults_to_key_gated_endpoint
```

Expected: PASS.

Commit:

```powershell
git add backend/app/services/predict_fun_event_source.py backend/tests/test_predict_fun_event_source.py backend/app/core/config.py backend/tests/test_config_defaults.py
git diff --cached --name-only
git commit -m "feat: add Predict.fun candidate source"
```

---

### Task 4: Discovery integration and source priority

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py`
- Modify: `backend/tests/test_event_intelligence_service.py`
- Modify: `backend/app/services/candidate_dedup_service.py`
- Modify: `backend/tests/test_candidate_dedup_service.py`

**Interfaces:**
- Consumes the three new `fetch_candidate_events()` functions.
- Produces `_collect_candidate_events()` integration with key gating.
- Produces priority order `Polymarket > Kalshi > Limitless > Opinion > Predict.fun`.

- [ ] **Step 1: Write failing discovery tests**

In `backend/tests/test_event_intelligence_service.py`, extend `CollectCandidateEventsCryptoOptInTests` with tests asserting:

```python
def test_onchain_sources_are_collected_with_key_gating(self):
    # Patch Polymarket/Kalshi/World Cup/Open Web/crypto to return [].
    # Patch Limitless, Opinion, and Predict.fun to return one candidate each.
    # Set LIMITLESS_SOURCE_ENABLED=True, OPINION_API_KEY="op-key",
    # PREDICT_FUN_API_KEY="pf-key".
    # Assert returned platforms are ["Limitless", "Opinion", "Predict.fun"].
```

```python
def test_credential_gated_onchain_sources_are_not_called_without_keys(self):
    # Patch all source fetches.
    # Set LIMITLESS_SOURCE_ENABLED=True, OPINION_API_KEY="", PREDICT_FUN_API_KEY="".
    # Assert Limitless was awaited once; Opinion and Predict.fun were not called.
```

```python
def test_probable_fetch_not_called(self):
    # Patch a create=True probable_event_source.fetch_candidate_events mock.
    # Run _collect_candidate_events with all optional sources disabled.
    # Assert probable_fetch.assert_not_called().
```

Run those three tests plus the existing Manifold regression:

```powershell
cd backend
python -m unittest tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_onchain_sources_are_collected_with_key_gating tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_credential_gated_onchain_sources_are_not_called_without_keys tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_probable_fetch_not_called tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_manifold_fetch_not_called
```

Expected: FAIL because `_collect_candidate_events()` does not call the new adapters yet.

- [ ] **Step 2: Write failing dedupe tests**

In `backend/tests/test_candidate_dedup_service.py`, add tests asserting:

```python
def test_onchain_market_priority_order(self):
    question = "Will BTC close above 150000 in 2026?"
    candidates = [
        {"question": question, "source": {"type": "prediction_market", "platform": "Predict.fun"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Opinion"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Limitless"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Kalshi"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Polymarket"}},
    ]
    self.assertEqual(dedupe_candidates(candidates)[0]["source"]["platform"], "Polymarket")
```

```python
def test_probable_and_manifold_do_not_outrank_active_onchain_sources(self):
    question = "Will ETH close above 5000 in 2026?"
    candidates = [
        {"question": question, "source": {"type": "prediction_market", "platform": "Probable"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Manifold"}},
        {"question": question, "source": {"type": "prediction_market", "platform": "Predict.fun"}},
    ]
    self.assertEqual(dedupe_candidates(candidates)[0]["source"]["platform"], "Predict.fun")
```

Run:

```powershell
cd backend
python -m unittest tests.test_candidate_dedup_service
```

Expected: FAIL until `_SOURCE_PRIORITY` includes new active sources.

- [ ] **Step 3: Integrate adapters**

In `_collect_candidate_events()` import:

```python
    from app.services.limitless_event_source import fetch_candidate_events as fetch_limitless_events
    from app.services.opinion_event_source import fetch_candidate_events as fetch_opinion_events
    from app.services.predict_fun_event_source import fetch_candidate_events as fetch_predict_fun_events
```

After the Polymarket/Kalshi `candidate_sources` list, add:

```python
    if settings.LIMITLESS_SOURCE_ENABLED:
        candidate_sources.append(("Limitless", fetch_limitless_events))
    if settings.OPINION_SOURCE_ENABLED and settings.OPINION_API_KEY:
        candidate_sources.append(("Opinion", fetch_opinion_events))
    if settings.PREDICT_FUN_SOURCE_ENABLED and settings.PREDICT_FUN_API_KEY:
        candidate_sources.append(("Predict.fun", fetch_predict_fun_events))
```

Do not import or call `probable_event_source`.

- [ ] **Step 4: Update source priority**

In `backend/app/services/candidate_dedup_service.py`, replace `_SOURCE_PRIORITY` with:

```python
_SOURCE_PRIORITY: dict[str, int] = {
    "Polymarket": 0,
    "Kalshi": 1,
    "Limitless": 2,
    "Opinion": 3,
    "Predict.fun": 4,
}
```

Update the module docstring priority text to match.

- [ ] **Step 5: Verify and commit discovery integration**

Run:

```powershell
cd backend
python -m unittest tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests tests.test_candidate_dedup_service
```

Expected: PASS.

Commit:

```powershell
git add backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py backend/app/services/candidate_dedup_service.py backend/tests/test_candidate_dedup_service.py
git diff --cached --name-only
git commit -m "feat: collect verified on-chain prediction sources"
```

---

### Task 5: Registry and frontend status semantics

**Files:**
- Modify: `backend/app/services/prediction_market_registry.py`
- Modify: `backend/tests/test_prediction_market_registry.py`
- Modify: `frontend/src/lib/prediction-market-platforms.ts`
- Modify: `frontend/src/components/detail/market-links.tsx`
- Modify: `frontend/src/components/detail/market-links.test.tsx`

**Interfaces:**
- Backend registry continues to produce `list_prediction_market_platforms()` and `active_discovery_platform_names()`.
- Frontend platform objects add `statusLabel: string`.

- [ ] **Step 1: Write failing backend registry tests**

Update `backend/tests/test_prediction_market_registry.py`:

```python
def test_active_discovery_platforms_include_default_live_sources_only(self):
    self.assertEqual(active_discovery_platform_names(), ["Polymarket", "Kalshi", "Limitless"])
```

Also assert:

- `platforms["limitless"].active_discovery is True`;
- Opinion status note contains `API key`;
- Predict.fun status note contains `API key`;
- Probable status note contains `requires verification`.

Run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry
```

Expected: FAIL until registry metadata is updated.

- [ ] **Step 2: Write failing frontend tests**

In `frontend/src/components/detail/market-links.test.tsx`, extend the first test:

```tsx
expect(screen.getAllByText("active")).toHaveLength(3);
expect(screen.getAllByText("API key required")).toHaveLength(2);
expect(screen.getByText("planned")).toBeInTheDocument();
```

Run:

```powershell
cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
```

Expected: FAIL until `statusLabel` is rendered.

- [ ] **Step 3: Update registry**

In `backend/app/services/prediction_market_registry.py`:

- set Limitless `active_discovery=True` and `status_note="Active public discovery source."`;
- set Opinion `status_note="API key source; adapter active when OPINION_API_KEY is configured."`;
- set Predict.fun `status_note="API key source; adapter active when PREDICT_FUN_API_KEY is configured."`;
- set Probable `status_note="Planned source; official adapter interface requires verification."`.

- [ ] **Step 4: Update frontend model and rendering**

In `frontend/src/lib/prediction-market-platforms.ts`, add:

```ts
statusLabel: string;
```

Set:

- Polymarket, Kalshi, Limitless: `statusLabel: "active"`;
- Opinion, Predict.fun: `statusLabel: "API key required"`;
- Probable: `statusLabel: "planned"`.

In `market-links.tsx`, replace the planned-only suffix with:

```tsx
<span>{p.chain}</span>
<span> · {p.statusLabel}</span>
```

- [ ] **Step 5: Verify and commit status semantics**

Run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry

cd ../frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```

Expected: PASS.

Commit:

```powershell
git add backend/app/services/prediction_market_registry.py backend/tests/test_prediction_market_registry.py frontend/src/lib/prediction-market-platforms.ts frontend/src/components/detail/market-links.tsx frontend/src/components/detail/market-links.test.tsx
git diff --cached --name-only
git commit -m "feat: update on-chain source status labels"
```

---

### Task 6: Docs and memory

**Files:**
- Modify: `README.md`
- Modify: `docs/user/USER_GUIDE.md`
- Modify: `docs/user/QUICK_START.md`
- Modify: `docs/dev/ARCHITECTURE.md`
- Modify: `SESSION_MEMORY_2026-07-08.md`

**Interfaces:**
- Produces docs that distinguish default-live, API-key gated, and planned-only sources.

- [ ] **Step 1: Update docs wording**

Use this English wording in `docs/user/QUICK_START.md` and `docs/dev/ARCHITECTURE.md`:

```markdown
Prediction-market discovery currently includes Polymarket, Kalshi, and the public Limitless adapter by default. Opinion and Predict.fun are live adapter-capable but require `OPINION_API_KEY` and `PREDICT_FUN_API_KEY`; without keys they fail closed and contribute no events. Probable remains planned-only until an official API, indexer, or contract-event interface is verified. The new on-chain adapters do not participate in auto-resolution yet.
```

Use this Chinese wording in `README.md` and `docs/user/USER_GUIDE.md`:

```markdown
当前预测市场发现默认包括 Polymarket、Kalshi 和公共 Limitless adapter。Opinion 和 Predict.fun 已具备真实 adapter 接入路径，但分别需要 `OPINION_API_KEY` 和 `PREDICT_FUN_API_KEY`；没有密钥时会 fail closed，不贡献事件。Probable 仍保持计划接入状态，直到官方 API、indexer 或合约事件接口被验证。新增链上 adapter 暂不参与自动结算。
```

Add config bullets where environment variables are listed:

```markdown
- `LIMITLESS_SOURCE_ENABLED` / `LIMITLESS_API_URL`: public Limitless market discovery.
- `OPINION_API_KEY`: enables Opinion Open API market discovery.
- `PREDICT_FUN_API_KEY`: enables Predict.fun beta API market discovery.
```

- [ ] **Step 2: Scan docs**

Run:

```powershell
Select-String -Path 'README.md','docs\user\USER_GUIDE.md','docs\user\QUICK_START.md','docs\dev\ARCHITECTURE.md' -Pattern 'Limitless|Opinion|Predict.fun|Probable|Manifold|auto-resolution|自动结算|planned-only|计划接入' -Context 1,1
```

Expected: Limitless is default-live; Opinion/Predict.fun are API-key gated; Probable is planned-only; Manifold is not current; auto-resolution is excluded.

- [ ] **Step 3: Append memory**

Append to `SESSION_MEMORY_2026-07-08.md`:

```markdown
## 2026-07-08 On-Chain Prediction Source Adapters Phase 2

Implemented live candidate-discovery adapters for verified on-chain sources:

- Limitless — Base, public active markets endpoint.
- Opinion — BNB Chain, Open API gated by `OPINION_API_KEY`.
- Predict.fun — BNB Chain, beta API gated by `PREDICT_FUN_API_KEY`.

Kept out of scope:

- Probable remains planned-only; no official interface was verified.
- No auto-resolution was added for these sources.
- No source performs trading, wallet signing, homepage scraping, or private API calls.
- Manifold remains inactive.
```

- [ ] **Step 4: Commit docs**

Run:

```powershell
git add README.md docs/user/USER_GUIDE.md docs/user/QUICK_START.md docs/dev/ARCHITECTURE.md
git diff --cached --name-only
git commit -m "docs: document on-chain source adapters"
```

Do not commit `SESSION_MEMORY_2026-07-08.md`; it is ignored by `.gitignore`.

---

### Task 7: Final verification

**Files:**
- No implementation files unless verification exposes a defect that is fixed with a new failing test first.

**Interfaces:**
- Consumes all Phase 2 work and produces verification evidence.

- [ ] **Step 1: Run backend verification**

Run:

```powershell
cd backend
python -m unittest tests.test_limitless_event_source tests.test_opinion_event_source tests.test_predict_fun_event_source tests.test_event_intelligence_service tests.test_candidate_dedup_service tests.test_config_defaults tests.test_prediction_market_registry
python -m compileall app scripts
```

Expected: all tests pass and compileall exits 0.

- [ ] **Step 2: Run frontend verification**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```

Expected: market-links tests pass and TypeScript exits 0.

- [ ] **Step 3: Run source scans**

Run:

```powershell
Select-String -Path 'backend\app\services\event_intelligence_service.py','backend\app\services\limitless_event_source.py','backend\app\services\opinion_event_source.py','backend\app\services\predict_fun_event_source.py','backend\app\services\candidate_dedup_service.py','frontend\src\lib\prediction-market-platforms.ts' -Pattern 'Limitless|Opinion|Predict.fun|Probable|Manifold|auto_resolve|fetch_resolved' -Context 1,1
```

Expected:

- Limitless, Opinion, and Predict.fun appear in adapter/discovery/frontend files.
- Probable does not appear in event discovery or adapter files.
- Manifold does not appear as an active source.
- New adapters do not define `fetch_resolved_markets`.

- [ ] **Step 4: Check task-specific status**

Run:

```powershell
git status --short -- backend/app/services/limitless_event_source.py backend/tests/test_limitless_event_source.py backend/app/services/opinion_event_source.py backend/tests/test_opinion_event_source.py backend/app/services/predict_fun_event_source.py backend/tests/test_predict_fun_event_source.py backend/app/core/config.py backend/tests/test_config_defaults.py backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py backend/app/services/candidate_dedup_service.py backend/tests/test_candidate_dedup_service.py backend/app/services/prediction_market_registry.py backend/tests/test_prediction_market_registry.py frontend/src/lib/prediction-market-platforms.ts frontend/src/components/detail/market-links.tsx frontend/src/components/detail/market-links.test.tsx README.md docs/user/USER_GUIDE.md docs/user/QUICK_START.md docs/dev/ARCHITECTURE.md SESSION_MEMORY_2026-07-08.md
```

Expected: no uncommitted changes for committed implementation/docs files; `SESSION_MEMORY_2026-07-08.md` may be modified and ignored.

## Self-Review

- Spec coverage: Limitless, Opinion, and Predict.fun adapters are covered; Probable is explicitly excluded; auto-resolution and trading are excluded.
- Test coverage: each adapter has missing-config, fetch, normalization, filtering, and fail-closed tests; discovery and dedupe integration have focused tests.
- Type consistency: all adapters expose `fetch_candidate_events(limit: int = 10) -> list[dict[str, Any]]`; candidate shape includes `source.chain`.
- Scope check: no runtime source-status API is added; frontend status labels are static and small.
- Source safety: credential-gated adapters skip network calls when keys are absent; Probable has no adapter file.

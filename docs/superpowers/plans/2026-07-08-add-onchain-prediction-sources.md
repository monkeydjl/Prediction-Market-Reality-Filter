# Add On-Chain Prediction Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Opinion, Limitless, Predict.fun, and Probable as visible planned prediction-market platforms without enabling unverified live discovery.

**Architecture:** Add a small backend platform registry and a matching frontend platform list. The registry is metadata-only in this phase: active discovery remains unchanged, while frontend market-link surfaces render the new platforms with chain labels and homepage/search links. Real source adapters remain out of scope until each platform's current official API/indexing interface is verified.

**Tech Stack:** Python `unittest`, FastAPI service modules, TypeScript React, Vitest/Testing Library.

## Global Constraints

- Phase 1 is registry/frontend visibility only.
- Do not add Opinion, Limitless, Predict.fun, or Probable to `_collect_candidate_events()` yet.
- Do not fabricate markets or baseline probabilities.
- Do not re-enable Manifold as an active source or frontend platform search entry.
- Preserve current active discovery behavior for Polymarket, Kalshi, Metaculus, World Cup, and Open Web.
- Use TDD: write failing tests before implementation changes.
- Current verified homepage/search observations for UI metadata:
  - Opinion: `https://opinion.trade/` redirects to `https://app.opinion.trade/trending`; page exposes a "Search Markets" entry.
  - Limitless: `https://limitless.exchange/` serves market categories and markets.
  - Predict.fun: `https://predict.fun/` serves a Markets page.
  - Probable: use `https://probable.finance/` as the user-requested homepage-style entry for Phase 1 only; keep `activeDiscovery=false` and do not build adapters from this assumption.

---

## File Structure

- Create `backend/app/services/prediction_market_registry.py`
  - Pure metadata registry for prediction-market platforms.
  - No network calls.
- Create `backend/tests/test_prediction_market_registry.py`
  - Backend registry behavior tests.
- Create `frontend/src/lib/prediction-market-platforms.ts`
  - Frontend platform list used by detail market links.
- Modify `frontend/src/components/detail/market-links.tsx`
  - Replace local `PLATFORMS` constant with imported platform list.
  - Display chain labels for on-chain platforms.
  - Keep source header behavior unchanged.
- Modify `frontend/src/components/detail/market-links.test.tsx`
  - Extend existing test to assert new platform links and chain labels.
  - Preserve existing assertion that Manifold is absent.
- Modify current docs:
  - `README.md`
  - `docs/user/USER_GUIDE.md`
  - `docs/user/QUICK_START.md`
  - `docs/dev/ARCHITECTURE.md`
- Append `SESSION_MEMORY_2026-07-08.md`
  - Record what was implemented and verified.

---

### Task 1: Backend prediction-market platform registry

**Files:**
- Create: `backend/app/services/prediction_market_registry.py`
- Create: `backend/tests/test_prediction_market_registry.py`

**Interfaces:**
- Produces:
  - `PredictionMarketPlatform` dataclass
  - `list_prediction_market_platforms() -> list[PredictionMarketPlatform]`
  - `active_discovery_platform_names() -> list[str]`
- Consumes: no existing modules.

- [ ] **Step 1: Write failing backend tests**

Create `backend/tests/test_prediction_market_registry.py`:

```python
import unittest

from app.services.prediction_market_registry import (
    active_discovery_platform_names,
    list_prediction_market_platforms,
)


class PredictionMarketRegistryTests(unittest.TestCase):
    def test_registry_contains_requested_onchain_platforms(self):
        platforms = {p.key: p for p in list_prediction_market_platforms()}

        self.assertEqual(platforms["opinion"].name, "Opinion")
        self.assertEqual(platforms["opinion"].chain, "BNB Chain")
        self.assertEqual(platforms["opinion"].homepage_url, "https://app.opinion.trade/trending")
        self.assertFalse(platforms["opinion"].active_discovery)

        self.assertEqual(platforms["limitless"].name, "Limitless")
        self.assertEqual(platforms["limitless"].chain, "Base")
        self.assertEqual(platforms["limitless"].homepage_url, "https://limitless.exchange/")
        self.assertFalse(platforms["limitless"].active_discovery)

        self.assertEqual(platforms["predict_fun"].name, "Predict.fun")
        self.assertEqual(platforms["predict_fun"].chain, "BNB Chain")
        self.assertEqual(platforms["predict_fun"].homepage_url, "https://predict.fun/")
        self.assertFalse(platforms["predict_fun"].active_discovery)

        self.assertEqual(platforms["probable"].name, "Probable")
        self.assertEqual(platforms["probable"].chain, "BNB Chain")
        self.assertEqual(platforms["probable"].homepage_url, "https://probable.finance/")
        self.assertFalse(platforms["probable"].active_discovery)

    def test_active_discovery_platforms_exclude_planned_onchain_sources_and_manifold(self):
        active = active_discovery_platform_names()

        self.assertEqual(active, ["Polymarket", "Kalshi"])
        self.assertNotIn("Opinion", active)
        self.assertNotIn("Limitless", active)
        self.assertNotIn("Predict.fun", active)
        self.assertNotIn("Probable", active)
        self.assertNotIn("Manifold", active)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.prediction_market_registry'`.

- [ ] **Step 3: Implement registry**

Create `backend/app/services/prediction_market_registry.py`:

```python
"""Prediction-market platform registry.

Metadata-only catalogue used by UI/source-status surfaces. Planned sources are
listed here before they are added to active discovery so the product can show
where support is heading without pretending unverified adapters exist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredictionMarketPlatform:
    key: str
    name: str
    chain: str
    homepage_url: str
    search_url_template: str | None
    active_discovery: bool
    status_note: str


_PLATFORMS: tuple[PredictionMarketPlatform, ...] = (
    PredictionMarketPlatform(
        key="polymarket",
        name="Polymarket",
        chain="Polygon",
        homepage_url="https://polymarket.com/markets",
        search_url_template="https://polymarket.com/markets?_q={query}",
        active_discovery=True,
        status_note="Active discovery source.",
    ),
    PredictionMarketPlatform(
        key="kalshi",
        name="Kalshi",
        chain="Off-chain",
        homepage_url="https://kalshi.com/markets",
        search_url_template="https://kalshi.com/markets?search={query}",
        active_discovery=True,
        status_note="Active discovery source.",
    ),
    PredictionMarketPlatform(
        key="opinion",
        name="Opinion",
        chain="BNB Chain",
        homepage_url="https://app.opinion.trade/trending",
        search_url_template=None,
        active_discovery=False,
        status_note="Planned source; adapter pending official interface verification.",
    ),
    PredictionMarketPlatform(
        key="limitless",
        name="Limitless",
        chain="Base",
        homepage_url="https://limitless.exchange/",
        search_url_template=None,
        active_discovery=False,
        status_note="Planned source; adapter pending official interface verification.",
    ),
    PredictionMarketPlatform(
        key="predict_fun",
        name="Predict.fun",
        chain="BNB Chain",
        homepage_url="https://predict.fun/",
        search_url_template=None,
        active_discovery=False,
        status_note="Planned source; adapter pending official interface verification.",
    ),
    PredictionMarketPlatform(
        key="probable",
        name="Probable",
        chain="BNB Chain",
        homepage_url="https://probable.finance/",
        search_url_template=None,
        active_discovery=False,
        status_note="Planned source; homepage shown in UI, adapter requires verification.",
    ),
)


def list_prediction_market_platforms() -> list[PredictionMarketPlatform]:
    return list(_PLATFORMS)


def active_discovery_platform_names() -> list[str]:
    return [platform.name for platform in _PLATFORMS if platform.active_discovery]
```

- [ ] **Step 4: Run backend registry tests**

Run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry
```

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit backend registry**

Commit only the registry and its test:

```powershell
git add backend/app/services/prediction_market_registry.py backend/tests/test_prediction_market_registry.py
git commit -m "feat: add prediction market platform registry"
```

If the working tree contains unrelated changes, verify `git diff --cached --name-only` lists only those two files before committing.

---

### Task 2: Frontend shared platform list and market-link rendering

**Files:**
- Create: `frontend/src/lib/prediction-market-platforms.ts`
- Modify: `frontend/src/components/detail/market-links.tsx`
- Modify: `frontend/src/components/detail/market-links.test.tsx`

**Interfaces:**
- Produces:
  - `PREDICTION_MARKET_PLATFORMS`
  - `marketPlatformUrl(platform, question) -> string`
- Consumes:
  - `MarketPanel({ record }: { record: EventRecord })`

- [ ] **Step 1: Write failing frontend tests**

Replace the first test in `frontend/src/components/detail/market-links.test.tsx` with:

```tsx
it("renders active and planned platform links with on-chain labels but not Manifold", () => {
  render(<MarketPanel record={record()} />);

  expect(screen.getByRole("link", { name: /Polymarket/i })).toHaveAttribute(
    "href",
    expect.stringContaining("polymarket.com"),
  );
  expect(screen.getByRole("link", { name: /Kalshi/i })).toHaveAttribute(
    "href",
    expect.stringContaining("kalshi.com"),
  );
  expect(screen.getByRole("link", { name: /Opinion/i })).toHaveAttribute(
    "href",
    "https://app.opinion.trade/trending",
  );
  expect(screen.getByRole("link", { name: /Limitless/i })).toHaveAttribute(
    "href",
    "https://limitless.exchange/",
  );
  expect(screen.getByRole("link", { name: /Predict\.fun/i })).toHaveAttribute(
    "href",
    "https://predict.fun/",
  );
  expect(screen.getByRole("link", { name: /Probable/i })).toHaveAttribute(
    "href",
    "https://probable.finance/",
  );

  expect(screen.getAllByText("BNB Chain")).toHaveLength(3);
  expect(screen.getByText("Base")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Manifold/i })).toBeNull();
});
```

Keep the existing test:

```tsx
it("still displays historical Manifold platform text", () => {
  render(<MarketPanel record={record()} />);

  expect(screen.getByText("Manifold")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
```

Expected: FAIL because Opinion, Limitless, Predict.fun, Probable, and chain labels do not render yet.

- [ ] **Step 3: Create frontend platform list**

Create `frontend/src/lib/prediction-market-platforms.ts`:

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

export const PREDICTION_MARKET_PLATFORMS: PredictionMarketPlatform[] = [
  {
    key: "polymarket",
    name: "Polymarket",
    chain: "Polygon",
    colorClass: "bg-[#555EEF]",
    homepageUrl: "https://polymarket.com/markets",
    searchUrl: (question) => `https://polymarket.com/markets?_q=${encodeURIComponent(question)}`,
    activeDiscovery: true,
  },
  {
    key: "kalshi",
    name: "Kalshi",
    chain: "Off-chain",
    colorClass: "bg-[#1ABAFF]",
    homepageUrl: "https://kalshi.com/markets",
    searchUrl: (question) => `https://kalshi.com/markets?search=${encodeURIComponent(question)}`,
    activeDiscovery: true,
  },
  {
    key: "opinion",
    name: "Opinion",
    chain: "BNB Chain",
    colorClass: "bg-[#F0B90B]",
    homepageUrl: "https://app.opinion.trade/trending",
    activeDiscovery: false,
  },
  {
    key: "limitless",
    name: "Limitless",
    chain: "Base",
    colorClass: "bg-[#0052FF]",
    homepageUrl: "https://limitless.exchange/",
    activeDiscovery: false,
  },
  {
    key: "predict_fun",
    name: "Predict.fun",
    chain: "BNB Chain",
    colorClass: "bg-[#7C3AED]",
    homepageUrl: "https://predict.fun/",
    activeDiscovery: false,
  },
  {
    key: "probable",
    name: "Probable",
    chain: "BNB Chain",
    colorClass: "bg-[#10B981]",
    homepageUrl: "https://probable.finance/",
    activeDiscovery: false,
  },
];

export function marketPlatformUrl(
  platform: PredictionMarketPlatform,
  question: string,
) {
  return platform.searchUrl ? platform.searchUrl(question) : platform.homepageUrl;
}
```

- [ ] **Step 4: Update MarketPanel implementation**

In `frontend/src/components/detail/market-links.tsx`:

1. Add import:

```tsx
import {
  PREDICTION_MARKET_PLATFORMS,
  marketPlatformUrl,
} from "@/lib/prediction-market-platforms";
```

2. Delete the local `PLATFORMS` constant.

3. Replace the platform render block:

```tsx
{PREDICTION_MARKET_PLATFORMS.map((p) => (
  <a
    key={p.key}
    href={marketPlatformUrl(p, question)}
    target="_blank"
    rel="noopener noreferrer"
    className="group flex items-center gap-3 rounded-md border border-border px-3 py-2 transition-colors hover:bg-secondary/60"
  >
    <span
      className={`flex size-6 shrink-0 items-center justify-center rounded text-[10px] font-bold text-white ${p.colorClass}`}
      aria-hidden="true"
    >
      {p.name[0]}
    </span>
    <span className="flex min-w-0 flex-1 flex-col">
      <span className="text-xs font-medium">{p.name}</span>
      <span className="text-[10px] text-muted-foreground">
        {p.chain}
        {!p.activeDiscovery ? " · planned" : ""}
      </span>
    </span>
    <ExternalLink className="size-3 text-muted-foreground transition-colors group-hover:text-foreground" />
  </a>
))}
```

4. Update the comment above the search links to:

```tsx
{/* Platform links: active sources plus planned on-chain sources */}
```

- [ ] **Step 5: Run frontend market-link tests**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
```

Expected: PASS, 2 tests.

- [ ] **Step 6: Commit frontend platform list**

Commit only frontend platform files:

```powershell
git add frontend/src/lib/prediction-market-platforms.ts frontend/src/components/detail/market-links.tsx frontend/src/components/detail/market-links.test.tsx
git commit -m "feat: show planned on-chain prediction markets"
```

If the working tree contains unrelated changes, verify `git diff --cached --name-only` before committing.

---

### Task 3: Current docs distinguish active and planned sources

**Files:**
- Modify: `README.md`
- Modify: `docs/user/USER_GUIDE.md`
- Modify: `docs/user/QUICK_START.md`
- Modify: `docs/dev/ARCHITECTURE.md`

**Interfaces:**
- Produces docs that say:
  - active discovery: Polymarket and Kalshi market sources, plus existing optional sources;
  - planned visible on-chain platforms: Opinion, Limitless, Predict.fun, Probable;
  - no live discovery adapters for those four yet.

- [ ] **Step 1: Write docs wording**

Add or update a concise source-status paragraph in each current doc.

Use this wording for English docs:

```markdown
Active prediction-market discovery currently uses Polymarket and Kalshi. Opinion
(BNB Chain), Predict.fun (BNB Chain), Probable (BNB Chain), and Limitless (Base)
are shown as planned on-chain platforms in the UI, but they do not contribute
events until their official APIs/indexers are verified and adapters are added.
```

Use this wording for Chinese docs:

```markdown
当前主动发现的预测市场来源是 Polymarket 和 Kalshi。Opinion（BNB Chain）、
Predict.fun（BNB Chain）、Probable（BNB Chain）和 Limitless（Base）会先作为
计划接入的链上平台显示在 UI 中；在官方 API / indexer / 合约事件接口验证并实现
adapter 之前，它们不会贡献新事件。
```

- [ ] **Step 2: Scan docs for misleading source language**

Run:

```powershell
Select-String -Path 'README.md','docs\user\USER_GUIDE.md','docs\user\QUICK_START.md','docs\dev\ARCHITECTURE.md' -Pattern 'Opinion|Limitless|Predict.fun|Probable|Manifold|active prediction-market|planned on-chain' -Context 1,1
```

Expected: docs mention the four platforms only as planned/on-chain UI-visible sources. Manifold should not appear as a current source.

- [ ] **Step 3: Commit docs**

Commit only current docs touched in this task:

```powershell
git add README.md docs/user/USER_GUIDE.md docs/user/QUICK_START.md docs/dev/ARCHITECTURE.md
git commit -m "docs: distinguish planned on-chain prediction sources"
```

If unrelated changes already exist in those docs, review `git diff --cached` carefully and commit only this task's hunks.

---

### Task 4: Final verification and memory update

**Files:**
- Modify: `SESSION_MEMORY_2026-07-08.md`

**Interfaces:**
- Consumes all prior task outputs.
- Produces verified Phase 1 implementation summary.

- [ ] **Step 1: Run backend verification**

Run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_manifold_fetch_not_called
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
Select-String -Path 'backend\app\services\event_intelligence_service.py','frontend\src\components\detail\market-links.tsx','frontend\src\lib\prediction-market-platforms.ts' -Pattern 'Opinion|Limitless|Predict.fun|Probable|Manifold' -Context 1,1
```

Expected:

- frontend platform list includes Opinion, Limitless, Predict.fun, Probable;
- event intelligence discovery does not include the four new planned sources;
- market-links does not include Manifold except via tests/historical source display elsewhere.

- [ ] **Step 4: Append memory**

Append this section to `SESSION_MEMORY_2026-07-08.md`:

```markdown
## 2026-07-08 On-Chain Prediction Source Registry Phase 1

Implemented Phase 1 for requested sources:

- Opinion — BNB Chain
- Predict.fun — BNB Chain
- Probable — BNB Chain
- Limitless — Base

Scope:

- Added backend metadata registry only.
- Added frontend platform list and market-link rendering.
- Did not add live discovery adapters.
- Did not add source weights or auto-resolution paths.
- Manifold remains inactive.

Verification:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_manifold_fetch_not_called
python -m compileall app scripts

cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```
```

- [ ] **Step 5: Commit memory**

Commit only memory if this project convention wants memory committed:

```powershell
git add SESSION_MEMORY_2026-07-08.md
git commit -m "docs: record on-chain source registry phase one"
```

If memory is not intended to be committed in this branch, leave it unstaged and mention that in the handoff.

---

## Final Verification

Before reporting completion, run:

```powershell
cd backend
python -m unittest tests.test_prediction_market_registry tests.test_event_intelligence_service.CollectCandidateEventsCryptoOptInTests.test_manifold_fetch_not_called
python -m compileall app scripts

cd frontend
npm.cmd test -- src/components/detail/market-links.test.tsx
npm.cmd run typecheck
```

Also run:

```powershell
git status --short -- backend/app/services/prediction_market_registry.py backend/tests/test_prediction_market_registry.py frontend/src/lib/prediction-market-platforms.ts frontend/src/components/detail/market-links.tsx frontend/src/components/detail/market-links.test.tsx README.md docs/user/USER_GUIDE.md docs/user/QUICK_START.md docs/dev/ARCHITECTURE.md SESSION_MEMORY_2026-07-08.md
```

Expected: only files from this plan are modified/staged after the implementation commits, plus any pre-existing unrelated workspace changes that were already present before execution.

## Self-Review

- Spec coverage: registry metadata, frontend visibility, active-discovery exclusion, docs, tests, and memory are covered.
- Placeholder scan: no TBD/TODO/unspecified implementation steps remain.
- Type consistency: backend `PredictionMarketPlatform` fields match frontend `PredictionMarketPlatform` concept; frontend uses `activeDiscovery`, backend uses `active_discovery` per language style.
- Scope check: real adapters, source weights, and auto-resolution are intentionally excluded from Phase 1 and called out in tests/docs.

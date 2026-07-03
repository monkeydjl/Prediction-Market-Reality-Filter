# Domain Reliability Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed `_all` domain reliability statistics back into `build_source_reliability` as a historical posterior fallback prior.

**Architecture:** `event_intelligence_service` remains the only production orchestrator: it best-effort loads `_all` domain reliability rows behind a new disabled-by-default flag and passes a projected list into the pure source reliability service. `source_reliability_service` remains pure and computes shrunk domain scores without importing settings or stores. Registry overrides keep priority over domain reliability; domain stats only fill gaps for sources not covered by the registry.

**Tech Stack:** Python 3.11, unittest/pytest, existing `app.core.config.Settings`, existing `app.services.source_reliability_service`, existing `app.memory.domain_reliability_store`.

## Global Constraints

- `source_reliability_service` must remain pure: no store imports, no settings imports, no I/O.
- `event_intelligence_service` is the only production call site that reads `domain_reliability_store`.
- `DOMAIN_RELIABILITY_FEEDBACK_ENABLED` defaults to `false`.
- `DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` defaults to `5`.
- Domain reliability feedback is inert unless `SOURCE_RELIABILITY_ENABLED=true`.
- Only `domain_reliability_store.get_stats(category="_all", min_samples=0)` rows are consumed.
- The orchestrator passes only `domain`, `sample_count`, and `correct_count` to `build_source_reliability`.
- Source score priority is registry `base_trust` first, domain stats shrunk score second, `_TIER_SCORES[tier]` third.
- Any registry match, including tier-only overrides with `base_trust=None`, blocks domain stats for that same source.
- Domain matching uses longest domain suffix match: `foo.reuters.com` matches `reuters.com`; longer suffix wins.
- Shrunk reliability is `(correct + 0.5 * K) / (sample + K)`.
- `sample <= 0`, non-integer `sample_count`, or `K <= 0` yields `None`; no exception propagates.
- `correct_count` is clamped to `[0, sample_count]` before shrinkage.
- `domain_stats_prior_affected` is omitted when `domain_stats_overrides is None`.
- `domain_stats_prior_affected` is present and `False` when `domain_stats_overrides` is a list but no valid source uses stats.
- Store load failure logs `logger.warning(..., exc_info=True)`, sets `domain_stats_overrides=None`, and does not block analysis.
- New tests are written first and run red before implementation.
- Keep existing direct `build_source_reliability` callers backward-compatible by giving new parameters defaults.
- Make small commits after each task.

---

## File Structure

- Modify `backend/app/core/config.py`
  - Adds `DOMAIN_RELIABILITY_FEEDBACK_ENABLED` and `DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` near the existing `DOMAIN_RELIABILITY_*` settings.
- Modify `backend/tests/test_domain_reliability_config.py`
  - Extends the existing config tests for the two new settings and defaults.
- Modify `backend/app/services/source_reliability_service.py`
  - Adds pure helpers `_shrunk_reliability`, `_domain_suffix_matches`, and `_match_domain_stats_override`.
  - Extends `build_source_reliability` with `domain_stats_overrides` and `domain_reliability_shrinkage_pseudocount`.
  - Adds `domain_stats_prior_affected` conditional output.
- Modify `backend/tests/test_source_reliability_service.py`
  - Adds pure helper, layered-priority, provenance-flag, suffix-match, dirty-data, and backward-compat tests.
- Modify `backend/app/services/event_intelligence_service.py`
  - Adds best-effort domain stats loading inside the existing source reliability block.
  - Passes the projected stats and shrinkage K into `build_source_reliability`.
- Modify `backend/tests/test_event_intelligence_service.py`
  - Adds orchestrator tests for flag-off, flag-on projection, store failure, source reliability off, and exact projection shape.

---

### Task 1: Config Flags

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_domain_reliability_config.py`

**Interfaces:**
- Produces: `settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED: bool`
- Produces: `settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT: int`
- Consumes: existing `_env_bool(name: str, default: str) -> bool` in `backend/app/core/config.py`

- [ ] **Step 1: Write the failing config tests**

Append assertions to the existing tests in `backend/tests/test_domain_reliability_config.py`:

```python
class TestDomainReliabilityConfig(unittest.TestCase):
    def test_settings_have_domain_reliability_fields(self):
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_TRACKING_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_DB_PATH"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_FEEDBACK_ENABLED"))
        self.assertTrue(hasattr(settings, "DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT"))

    def test_default_values(self):
        self.assertFalse(settings.DOMAIN_RELIABILITY_TRACKING_ENABLED)
        self.assertTrue(settings.DOMAIN_RELIABILITY_DB_PATH.endswith("domain_reliability.db"))
        self.assertEqual(settings.DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES, 5)
        self.assertFalse(settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED)
        self.assertEqual(settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT, 5)
```

- [ ] **Step 2: Run the config test and verify it fails**

Run:

```powershell
cd backend
python -m pytest tests/test_domain_reliability_config.py -q
```

Expected: FAIL because `DOMAIN_RELIABILITY_FEEDBACK_ENABLED` and `DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT` do not exist.

- [ ] **Step 3: Add the settings**

In `backend/app/core/config.py`, extend the existing domain reliability block after `DOMAIN_RELIABILITY_CONFIDENCE_MIN_SAMPLES`:

```python
    # Domain reliability feedback (LATER #2 v2): feed per-domain historical
    # accuracy back into build_source_reliability as a layered prior.
    DOMAIN_RELIABILITY_FEEDBACK_ENABLED: bool = _env_bool(
        "DOMAIN_RELIABILITY_FEEDBACK_ENABLED", "false"
    )
    DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT: int = int(
        os.getenv("DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT", "5")
    )
```

- [ ] **Step 4: Run the config test and verify it passes**

Run:

```powershell
cd backend
python -m pytest tests/test_domain_reliability_config.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/core/config.py backend/tests/test_domain_reliability_config.py
git commit -m "feat: add domain reliability feedback settings"
```

---

### Task 2: Pure Domain Stats Prior In Source Reliability

**Files:**
- Modify: `backend/app/services/source_reliability_service.py`
- Modify: `backend/tests/test_source_reliability_service.py`

**Interfaces:**
- Produces: `_shrunk_reliability(correct: Any, sample: Any, K: int) -> float | None`
- Produces: `_domain_suffix_matches(domain: str, pattern: str) -> bool`
- Produces: `_match_domain_stats_override(domain: str, overrides: list[dict[str, Any]]) -> dict[str, Any] | None`
- Modifies: `build_source_reliability(..., domain_stats_overrides: list[dict[str, Any]] | None = None, domain_reliability_shrinkage_pseudocount: int = 5) -> dict[str, Any] | None`
- Produces result field: `domain_stats_prior_affected: bool` only when `domain_stats_overrides is not None`

- [ ] **Step 1: Import the helper under test**

Update the imports at the top of `backend/tests/test_source_reliability_service.py`:

```python
from app.services.source_reliability_service import (
    _shrunk_reliability,
    build_source_reliability,
    classify_source_tier,
    extract_domain,
)
```

- [ ] **Step 2: Add shrunk reliability failing tests**

Add this test class near the existing pure-helper tests:

```python
class TestDomainReliabilityShrinkage(unittest.TestCase):
    def test_shrinks_toward_neutral_with_positive_sample(self):
        self.assertAlmostEqual(
            _shrunk_reliability(correct=40, sample=50, K=5),
            (40 + 0.5 * 5) / (50 + 5),
            places=6,
        )

    def test_zero_sample_returns_none(self):
        self.assertIsNone(_shrunk_reliability(correct=0, sample=0, K=5))

    def test_zero_or_negative_k_returns_none(self):
        self.assertIsNone(_shrunk_reliability(correct=5, sample=10, K=0))
        self.assertIsNone(_shrunk_reliability(correct=5, sample=10, K=-1))

    def test_correct_count_clamped_low(self):
        self.assertAlmostEqual(
            _shrunk_reliability(correct=-3, sample=10, K=5),
            (0 + 0.5 * 5) / (10 + 5),
            places=6,
        )

    def test_correct_count_clamped_high(self):
        self.assertAlmostEqual(
            _shrunk_reliability(correct=30, sample=10, K=5),
            (10 + 0.5 * 5) / (10 + 5),
            places=6,
        )

    def test_non_integer_sample_returns_none(self):
        self.assertIsNone(_shrunk_reliability(correct=5, sample="10", K=5))
        self.assertIsNone(_shrunk_reliability(correct=5, sample=10.0, K=5))
```

- [ ] **Step 3: Run the helper tests and verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_source_reliability_service.py::TestDomainReliabilityShrinkage -q
```

Expected: FAIL because `_shrunk_reliability` is not defined or not importable.

- [ ] **Step 4: Implement shrinkage and suffix-match helpers**

In `backend/app/services/source_reliability_service.py`, add these helpers near `_match_registry_override`:

```python
def _shrunk_reliability(correct: Any, sample: Any, K: int) -> float | None:
    """Return Beta(0.5K, 0.5K) posterior mean, or None when unusable."""
    if isinstance(sample, bool) or not isinstance(sample, int):
        return None
    if sample <= 0 or K <= 0:
        return None
    if isinstance(correct, bool) or not isinstance(correct, int):
        correct_int = 0
    else:
        correct_int = correct
    correct_int = max(0, min(correct_int, sample))
    return (correct_int + 0.5 * K) / (sample + K)


def _domain_suffix_matches(domain: str, pattern: str) -> bool:
    """True when domain equals pattern or is a subdomain of pattern."""
    domain_lower = (domain or "").strip().lower()
    pattern_lower = (pattern or "").strip().lower()
    if not domain_lower or not pattern_lower:
        return False
    return domain_lower == pattern_lower or domain_lower.endswith("." + pattern_lower)


def _match_domain_stats_override(
    domain: str,
    overrides: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the longest domain suffix match in domain stats overrides."""
    best: dict[str, Any] | None = None
    best_len = -1
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("domain")
        if not isinstance(pattern, str):
            continue
        pattern = pattern.strip().lower()
        if _domain_suffix_matches(domain, pattern) and len(pattern) > best_len:
            best = entry
            best_len = len(pattern)
    return best
```

- [ ] **Step 5: Run the helper tests and verify they pass**

Run:

```powershell
cd backend
python -m pytest tests/test_source_reliability_service.py::TestDomainReliabilityShrinkage -q
```

Expected: PASS.

- [ ] **Step 6: Add domain stats prior behavior failing tests**

Add this test class near the existing `build_source_reliability` tests:

```python
class TestDomainStatsPrior(unittest.TestCase):
    def test_domain_stats_param_none_omits_new_field(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Random Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Random Blog", url="https://random.example/1"),
            ],
            min_domain_diversity=1,
            min_sources=1,
        ))
        self.assertNotIn("domain_stats_prior_affected", result)

    def test_empty_domain_stats_list_emits_false_flag(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Random Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Random Blog", url="https://random.example/1"),
            ],
            domain_stats_overrides=[],
            min_domain_diversity=1,
            min_sources=1,
        ))
        self.assertFalse(result["domain_stats_prior_affected"])

    def test_stats_hit_uses_shrunk_score_and_sets_flag(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 50, "correct_count": 40},
            ],
            domain_reliability_shrinkage_pseudocount=5,
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertTrue(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.6791, places=4)

    def test_all_zero_sample_stats_fall_back_to_tier_and_flag_false(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 0, "correct_count": 0},
            ],
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertFalse(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.45, places=4)

    def test_k_zero_falls_back_to_tier_and_flag_false(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 50, "correct_count": 40},
            ],
            domain_reliability_shrinkage_pseudocount=0,
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertFalse(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.45, places=4)

    def test_registry_base_trust_has_priority_over_domain_stats(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            registry_overrides=[
                {
                    "pattern_type": "domain",
                    "pattern": "example.com",
                    "tier": "trusted",
                    "base_trust": 0.95,
                },
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 50, "correct_count": 0},
            ],
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertTrue(result["source_prior_affected"])
        self.assertFalse(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.95, places=4)

    def test_registry_tier_only_match_blocks_domain_stats(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            registry_overrides=[
                {
                    "pattern_type": "domain",
                    "pattern": "example.com",
                    "tier": "trusted",
                    "base_trust": None,
                },
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 50, "correct_count": 0},
            ],
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertTrue(result["source_prior_affected"])
        self.assertFalse(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.91, places=4)

    def test_longest_suffix_match_wins(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Subdomain Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(
                    source="Subdomain Blog",
                    url="https://foo.example.com/story",
                ),
            ],
            domain_stats_overrides=[
                {"domain": "example.com", "sample_count": 50, "correct_count": 0},
                {"domain": "foo.example.com", "sample_count": 50, "correct_count": 50},
            ],
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertTrue(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.7527, places=4)

    def test_dirty_override_rows_are_skipped(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[
                _breakdown_item(source="Unknown Blog", credibility=0.8, strength=0.7),
            ],
            evidence_items=[
                _evidence_item(source="Unknown Blog", url="https://example.com/story"),
            ],
            domain_stats_overrides=[
                {"sample_count": 50, "correct_count": 50},
                {"domain": "example.com", "sample_count": "50", "correct_count": 50},
            ],
            min_domain_diversity=1,
            min_sources=1,
            min_trusted_ratio=0.0,
            score_threshold=0.0,
        ))
        self.assertFalse(result["domain_stats_prior_affected"])
        self.assertAlmostEqual(result["overall_score"], 0.45, places=4)
```

- [ ] **Step 7: Run the new behavior tests and verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_source_reliability_service.py::TestDomainStatsPrior -q
```

Expected: FAIL because `build_source_reliability` does not accept `domain_stats_overrides` yet.

- [ ] **Step 8: Extend `build_source_reliability` signature and docstring**

In `backend/app/services/source_reliability_service.py`, update the signature:

```python
def build_source_reliability(
    *,
    evidence_breakdown: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    raw_direction: str | None,
    enabled: bool,
    score_threshold: float,
    min_trusted_ratio: float,
    min_domain_diversity: int,
    min_sources: int,
    registry_overrides: list[dict[str, Any]] | None = None,
    domain_stats_overrides: list[dict[str, Any]] | None = None,
    domain_reliability_shrinkage_pseudocount: int = 5,
) -> dict[str, Any] | None:
```

Add this paragraph to the docstring after the registry paragraph:

```python
    ``domain_stats_overrides`` is an optional list of projected
    domain-reliability rows (each a dict with ``domain``, ``sample_count``,
    ``correct_count``). When provided, sources not covered by the registry use
    a shrunk historical reliability score as the tier-score prior. The key
    ``domain_stats_prior_affected`` is emitted only when the parameter is not
    None; its value is True only when at least one source used a valid shrunk
    score.
```

- [ ] **Step 9: Track registry match and domain stats score per source**

Inside the per-item loop, initialize both provenance flags before the loop:

```python
    source_prior_affected = False
    domain_stats_prior_affected = False
```

Replace the registry block with this block:

```python
        registry_matched = False
        if registry_overrides:
            override = _match_registry_override(source_name, domain, registry_overrides)
            if override is not None:
                registry_matched = True
                tier = override.get("tier") or tier
                if override.get("base_trust") is not None:
                    base_trust_override = override["base_trust"]
                else:
                    base_trust_override = None
                source_prior_affected = True
            else:
                base_trust_override = None
        else:
            base_trust_override = None

        domain_stats_score = None
        if domain_stats_overrides is not None and not registry_matched:
            stats_override = _match_domain_stats_override(domain, domain_stats_overrides)
            if stats_override is not None:
                domain_stats_score = _shrunk_reliability(
                    correct=stats_override.get("correct_count"),
                    sample=stats_override.get("sample_count"),
                    K=domain_reliability_shrinkage_pseudocount,
                )
                if domain_stats_score is not None:
                    domain_stats_prior_affected = True
```

When creating a new `source_agg` entry, store the domain stats score:

```python
                "base_trust_override": base_trust_override,
                "domain_stats_score": domain_stats_score,
```

- [ ] **Step 10: Apply layered prior in weighted score**

Replace the `weighted_tier_sum = sum(...)` expression with an explicit loop:

```python
        weighted_tier_sum = 0.0
        for agg in source_agg.values():
            if agg.get("base_trust_override") is not None:
                prior_score = agg["base_trust_override"]
            elif agg.get("domain_stats_score") is not None:
                prior_score = agg["domain_stats_score"]
            else:
                prior_score = _TIER_SCORES.get(agg["tier"], 0.20)
            weighted_tier_sum += prior_score * agg["article_count"]
        weighted_avg_tier_score = weighted_tier_sum / total_articles
```

- [ ] **Step 11: Emit `domain_stats_prior_affected` conditionally**

Near the existing `source_prior_affected` result block, add:

```python
    if domain_stats_overrides is not None:
        result["domain_stats_prior_affected"] = domain_stats_prior_affected
```

- [ ] **Step 12: Update the registry helper docstring wording**

Change `_match_registry_override` docstring from "longest-prefix domain match" to "longest domain suffix match":

```python
    """Find the longest domain suffix match or first source_name substring
    match in ``overrides``. Returns the override dict or None.
    """
```

- [ ] **Step 13: Run the pure source reliability tests**

Run:

```powershell
cd backend
python -m pytest tests/test_source_reliability_service.py -q
```

Expected: PASS.

- [ ] **Step 14: Run direct caller regression tests**

Run:

```powershell
cd backend
python -m pytest tests/test_decision_quality_engine_integration.py -q
```

Expected: PASS. This verifies direct `build_source_reliability` calls still work without passing the new parameters.

- [ ] **Step 15: Commit Task 2**

```powershell
git add backend/app/services/source_reliability_service.py backend/tests/test_source_reliability_service.py
git commit -m "feat: apply domain reliability stats prior"
```

---

### Task 3: Orchestrator Domain Stats Loading

**Files:**
- Modify: `backend/app/services/event_intelligence_service.py`
- Modify: `backend/tests/test_event_intelligence_service.py`

**Interfaces:**
- Consumes: `settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED`
- Consumes: `settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT`
- Consumes: `domain_reliability_store.get_stats(category="_all", min_samples=0) -> list[dict[str, Any]]`
- Produces call kwargs: `domain_stats_overrides` and `domain_reliability_shrinkage_pseudocount`

- [ ] **Step 1: Add helper support in source reliability overlay tests**

In `SourceReliabilityOverlayTests._run_analyze` inside `backend/tests/test_event_intelligence_service.py`, add these optional parameters:

```python
        domain_feedback_enabled: bool = False,
        domain_stats_rows: list[dict] | None = None,
        domain_stats_side_effect=None,
        capture_build_call: bool = False,
```

Inside its `ExitStack`, after the existing source reliability settings patches, add:

```python
            stack.enter_context(patch.object(
                eis.settings,
                "DOMAIN_RELIABILITY_FEEDBACK_ENABLED",
                domain_feedback_enabled,
            ))
            stack.enter_context(patch.object(
                eis.settings,
                "DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT",
                5,
            ))
            if domain_stats_rows is not None or domain_stats_side_effect is not None:
                get_stats = stack.enter_context(patch(
                    "app.memory.domain_reliability_store.get_stats",
                    return_value=domain_stats_rows or [],
                    side_effect=domain_stats_side_effect,
                ))
            else:
                get_stats = None
            if capture_build_call:
                build_sr = stack.enter_context(patch(
                    "app.services.source_reliability_service.build_source_reliability",
                    return_value={
                        "overall_score": 0.9,
                        "source_count": 2,
                        "domain_diversity": 2,
                        "trusted_source_ratio": 1.0,
                        "official_source_count": 0,
                        "unknown_source_ratio": 0.0,
                        "source_breakdown": [],
                        "downgrade_reason": None,
                        "raw_direction": "YES",
                        "suggested_direction": "YES",
                        "downgraded": False,
                        "applied_to_displayed_direction": False,
                    },
                ))
            else:
                build_sr = None
```

Replace the existing `return _run(eis.analyze_event(...))` tail with:

```python
            record = _run(eis.analyze_event(
                "Will X happen?",
                baseline_probability=50,
                news_context="direction: support",
                source=source,
                sentiment_profile=sentiment or self.SENTIMENT,
                filtered_articles=filtered_articles or self.FILTERED_ARTICLES_2SRC,
            ))
            if capture_build_call:
                return record, build_sr, get_stats
            return record
```

Keep the existing `build_sr_side_effect` branch by applying it only when `capture_build_call` is false:

```python
            if build_sr_side_effect is not None and not capture_build_call:
                stack.enter_context(patch(
                    "app.services.source_reliability_service.build_source_reliability",
                    side_effect=build_sr_side_effect,
                ))
```

- [ ] **Step 2: Add flag-off failing test**

Add this test to `SourceReliabilityOverlayTests`:

```python
    def test_domain_feedback_flag_off_passes_none_and_does_not_load_store(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=False,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                },
            ],
            capture_build_call=True,
        )
        self.assertIn("source_reliability", record)
        self.assertIsNotNone(build_sr)
        self.assertIsNotNone(get_stats)
        get_stats.assert_not_called()
        self.assertIsNone(build_sr.call_args.kwargs["domain_stats_overrides"])
```

- [ ] **Step 3: Add flag-on projection failing test**

```python
    def test_domain_feedback_flag_on_projects_stats_rows(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=True,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                    "wrong_count": 2,
                    "reliability_score": 0.8,
                    "credibility_sum": 7.5,
                },
                {
                    "domain": "bloomberg.com",
                    "category": "_all",
                    "sample_count": 5,
                    "correct_count": 2,
                    "wrong_count": 3,
                },
            ],
            capture_build_call=True,
        )
        self.assertIn("source_reliability", record)
        get_stats.assert_called_once_with(category="_all", min_samples=0)
        self.assertEqual(
            build_sr.call_args.kwargs["domain_stats_overrides"],
            [
                {"domain": "reuters.com", "sample_count": 10, "correct_count": 8},
                {"domain": "bloomberg.com", "sample_count": 5, "correct_count": 2},
            ],
        )
        self.assertEqual(
            build_sr.call_args.kwargs["domain_reliability_shrinkage_pseudocount"],
            5,
        )
```

- [ ] **Step 4: Add store failure failing test**

```python
    def test_domain_feedback_store_failure_is_best_effort(self):
        record, build_sr, get_stats = self._run_analyze(
            sr_enabled=True,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=True,
            domain_stats_side_effect=RuntimeError("db unavailable"),
            capture_build_call=True,
        )
        self.assertIn("source_reliability", record)
        get_stats.assert_called_once_with(category="_all", min_samples=0)
        self.assertIsNone(build_sr.call_args.kwargs["domain_stats_overrides"])
```

- [ ] **Step 5: Add source reliability off failing test**

```python
    def test_domain_feedback_does_not_load_when_source_reliability_disabled(self):
        record = self._run_analyze(
            sr_enabled=False,
            source={"type": "prediction_market", "platform": "Polymarket"},
            domain_feedback_enabled=True,
            domain_stats_rows=[
                {
                    "domain": "reuters.com",
                    "category": "_all",
                    "sample_count": 10,
                    "correct_count": 8,
                },
            ],
        )
        self.assertNotIn("source_reliability", record)
```

This test is paired with the helper setup from Step 1: because the helper patches `get_stats`, the store mock would raise assertion failures only through explicit calls in the other tests. The absence of `source_reliability` here verifies the outer guard keeps the block inert.

- [ ] **Step 6: Run the orchestrator tests and verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/test_event_intelligence_service.py::SourceReliabilityOverlayTests -q
```

Expected: FAIL because `domain_stats_overrides` and `domain_reliability_shrinkage_pseudocount` are not passed yet.

- [ ] **Step 7: Add the orchestrator load block**

In `backend/app/services/event_intelligence_service.py`, inside the existing `if settings.SOURCE_RELIABILITY_ENABLED:` block and after the registry block, add:

```python
            domain_stats_overrides: list[dict[str, Any]] | None = None
            if settings.DOMAIN_RELIABILITY_FEEDBACK_ENABLED:
                try:
                    from app.memory import domain_reliability_store
                    rows = domain_reliability_store.get_stats(
                        category="_all",
                        min_samples=0,
                    )
                    domain_stats_overrides = [
                        {
                            "domain": r["domain"],
                            "sample_count": r["sample_count"],
                            "correct_count": r["correct_count"],
                        }
                        for r in rows
                    ]
                except Exception as exc:
                    logger.warning(
                        "domain_reliability load failed, continuing without "
                        "stats prior: %s",
                        exc,
                        exc_info=True,
                    )
                    domain_stats_overrides = None
```

- [ ] **Step 8: Pass the new kwargs into `build_source_reliability`**

Update the call at `backend/app/services/event_intelligence_service.py` around the existing `registry_overrides` kwarg:

```python
                registry_overrides=registry_overrides,
                domain_stats_overrides=domain_stats_overrides,
                domain_reliability_shrinkage_pseudocount=(
                    settings.DOMAIN_RELIABILITY_SHRINKAGE_PSEUDOCOUNT
                ),
```

- [ ] **Step 9: Run the orchestrator tests and verify they pass**

Run:

```powershell
cd backend
python -m pytest tests/test_event_intelligence_service.py::SourceReliabilityOverlayTests -q
```

Expected: PASS.

- [ ] **Step 10: Run the full event intelligence test file**

Run:

```powershell
cd backend
python -m pytest tests/test_event_intelligence_service.py -q
```

Expected: PASS.

- [ ] **Step 11: Commit Task 3**

```powershell
git add backend/app/services/event_intelligence_service.py backend/tests/test_event_intelligence_service.py
git commit -m "feat: load domain reliability stats for source reliability"
```

---

### Task 4: Full Regression And Final Review

**Files:**
- Verify only: `backend/app/core/config.py`
- Verify only: `backend/app/services/source_reliability_service.py`
- Verify only: `backend/app/services/event_intelligence_service.py`
- Verify only: `backend/tests/test_domain_reliability_config.py`
- Verify only: `backend/tests/test_source_reliability_service.py`
- Verify only: `backend/tests/test_event_intelligence_service.py`

**Interfaces:**
- Consumes: all interfaces from Tasks 1-3.
- Produces: verified implementation branch with passing targeted and full backend tests.

- [ ] **Step 1: Run targeted regression suite**

Run:

```powershell
cd backend
python -m pytest tests/test_domain_reliability_config.py tests/test_source_reliability_service.py tests/test_event_intelligence_service.py tests/test_decision_quality_engine_integration.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full backend tests**

Run:

```powershell
cd backend
python -m pytest -x -q
```

Expected: PASS with zero failures.

- [ ] **Step 3: Inspect source reliability output shape manually**

Run this from `backend`:

```powershell
python -c "from app.services.source_reliability_service import build_source_reliability; kwargs=dict(evidence_breakdown=[{'source':'Random','credibility':0.8,'strength':0.7}], evidence_items=[{'source':'Random','url':'https://example.com/x'}], raw_direction='YES', enabled=True, score_threshold=0.0, min_trusted_ratio=0.0, min_domain_diversity=1, min_sources=1); print('domain_stats_prior_affected' in build_source_reliability(**kwargs)); print(build_source_reliability(**kwargs, domain_stats_overrides=[])['domain_stats_prior_affected'])"
```

Expected output:

```text
False
False
```

- [ ] **Step 4: Review diff for boundary violations**

Run:

```powershell
git diff -- backend/app/services/source_reliability_service.py backend/app/services/event_intelligence_service.py backend/app/core/config.py
```

Check these exact conditions in the diff:

- `source_reliability_service.py` does not import `settings`.
- `source_reliability_service.py` does not import `domain_reliability_store`.
- `event_intelligence_service.py` imports `domain_reliability_store` only inside the guarded best-effort block.
- Store load warning includes `exc_info=True`.
- `domain_stats_prior_affected` is emitted only when `domain_stats_overrides is not None`.

- [ ] **Step 5: Review dirty worktree**

Run:

```powershell
git status --short
```

Expected: only files intentionally changed by this plan are modified or staged. Existing unrelated `.sdd` and earlier plan/report files must not be reverted or folded into this feature commit.

- [ ] **Step 6: Final commit if Task 4 changed any files**

If Task 4 only verified and did not edit files, skip this commit. If Task 4 made small test or comment fixes, commit them:

```powershell
git add backend/app/core/config.py backend/app/services/source_reliability_service.py backend/app/services/event_intelligence_service.py backend/tests/test_domain_reliability_config.py backend/tests/test_source_reliability_service.py backend/tests/test_event_intelligence_service.py
git commit -m "test: verify domain reliability feedback"
```

---

## Self-Review Checklist

- [x] Spec coverage: Tasks 1-3 cover config, pure service, orchestrator, provenance, suffix matching, shrinkage, dirty data, and best-effort error handling.
- [x] Regression coverage: Task 4 includes targeted tests, `test_decision_quality_engine_integration.py`, and full `python -m pytest -x -q`.
- [x] Placeholder scan: no forbidden placeholder markers or unspecified test categories.
- [x] Type consistency: `domain_stats_overrides`, `domain_reliability_shrinkage_pseudocount`, and `domain_stats_prior_affected` are named consistently across tests and implementation.
- [x] Boundary check: pure source reliability service never imports settings or stores.
- [x] Edge semantics: `K <= 0` and unusable samples degrade to `None`, not exceptions.
- [x] Registry priority: registry match blocks domain stats for the same source, including tier-only registry entries.

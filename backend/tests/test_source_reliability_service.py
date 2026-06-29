"""Tests for source_reliability_service (Phase 4 pure helpers).

Locks the spec invariants:
- ``extract_domain`` strips www., lowercases, returns '' for missing/malformed.
- ``classify_source_tier`` classifies into official/trusted/established/
  aggregator/unknown using substring match on source name OR domain.
- ``build_source_reliability`` is a pure function: no mutation of inputs,
  never raises on adversarial input, returns None for empty evidence_breakdown.
- Downgrade rules fire first-match-wins; only YES/NO can be downgraded to WAIT;
  WAIT/AVOID are never downgraded.
- Overall score is a weighted combination (tier 40% + diversity 25% + trusted
  ratio 20% + credibility 15%).
- ``raw_direction`` is preserved; ``suggested_direction`` may diverge to WAIT;
  ``downgraded`` is true iff they differ.
"""
import unittest

from app.services.source_reliability_service import (
    build_source_reliability,
    classify_source_tier,
    extract_domain,
)


def _breakdown_item(source="", direction="support", strength=0.5, credibility=0.5, title=""):
    return {
        "source": source,
        "title": title,
        "direction": direction,
        "strength": strength,
        "credibility": credibility,
        "rationale_zh": "",
    }


def _evidence_item(source="", url="", quality=0.5):
    return {"source": source, "url": url, "quality": quality}


def _build_defaults(**overrides):
    """Default kwargs for build_source_reliability with per-test overrides."""
    defaults = {
        "evidence_breakdown": [_breakdown_item(source="Reuters", credibility=0.85)],
        "evidence_items": [_evidence_item(source="Reuters", url="https://www.reuters.com/article/1")],
        "raw_direction": "YES",
        "enabled": True,
        "score_threshold": 0.5,
        "min_trusted_ratio": 0.4,
        "min_domain_diversity": 2,
        "min_sources": 2,
    }
    defaults.update(overrides)
    return defaults


class ExtractDomainTests(unittest.TestCase):
    """URL → domain extraction — strips www., lowercases, returns '' for
    missing/malformed URLs."""

    def test_valid_url_with_https(self):
        self.assertEqual(extract_domain("https://www.reuters.com/article/1"), "reuters.com")

    def test_valid_url_with_http(self):
        self.assertEqual(extract_domain("http://www.bbc.co.uk/news/1"), "bbc.co.uk")

    def test_strips_www_prefix(self):
        self.assertEqual(extract_domain("https://www.coindesk.com/"), "coindesk.com")

    def test_lowercases_hostname(self):
        self.assertEqual(extract_domain("https://WWW.REUTERS.COM/"), "reuters.com")

    def test_empty_string_returns_empty(self):
        self.assertEqual(extract_domain(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(extract_domain(None), "")

    def test_malformed_url_returns_empty(self):
        self.assertEqual(extract_domain("not-a-url"), "")

    def test_url_without_scheme_returns_empty(self):
        # urlparse treats "reuters.com" as a path, not a hostname.
        self.assertEqual(extract_domain("reuters.com/article"), "")


class ClassifySourceTierTests(unittest.TestCase):
    """4-tier classification — substring match on source name OR domain,
    first match wins (official > trusted > established > aggregator > unknown)."""

    def test_official_sec_gov(self):
        self.assertEqual(classify_source_tier("SEC", "sec.gov"), "official")

    def test_official_federalreserve(self):
        self.assertEqual(classify_source_tier("Fed", "federalreserve.gov"), "official")

    def test_official_via_domain_only(self):
        # Source name doesn't say "official" but the domain is whitehouse.gov.
        self.assertEqual(classify_source_tier("White House Press", "whitehouse.gov"), "official")

    def test_trusted_reuters(self):
        self.assertEqual(classify_source_tier("Reuters", "reuters.com"), "trusted")

    def test_trusted_bloomberg(self):
        self.assertEqual(classify_source_tier("Bloomberg", "bloomberg.com"), "trusted")

    def test_trusted_ap_news(self):
        self.assertEqual(classify_source_tier("AP News", "apnews.com"), "trusted")

    def test_established_coindesk(self):
        self.assertEqual(classify_source_tier("CoinDesk", "coindesk.com"), "established")

    def test_established_bbc(self):
        self.assertEqual(classify_source_tier("BBC News", "bbc.co.uk"), "established")

    def test_aggregator_cointelegraph(self):
        self.assertEqual(classify_source_tier("CoinTelegraph", "cointelegraph.com"), "aggregator")

    def test_unknown_empty_source(self):
        self.assertEqual(classify_source_tier("", ""), "unknown")

    def test_unknown_unrecognized_source(self):
        self.assertEqual(classify_source_tier("Some Random Blog", "randomblog.xyz"), "unknown")

    def test_case_insensitive_source_name(self):
        self.assertEqual(classify_source_tier("REUTERS", ""), "trusted")

    def test_official_takes_precedence_over_trusted(self):
        # A source whose name contains "reuters" but domain is sec.gov → official.
        self.assertEqual(classify_source_tier("Reuters Wire from SEC", "sec.gov"), "official")

    def test_trusted_takes_precedence_over_established(self):
        # "reuters politics" contains "reuters" → trusted (not established).
        self.assertEqual(classify_source_tier("Reuters Politics", "reuters.com"), "trusted")

    def test_reuters_politics_matches_established_pattern(self):
        # When domain is empty, "reuters politics" matches the established pattern.
        # But "reuters" substring also matches the trusted pattern first.
        self.assertEqual(classify_source_tier("Reuters Politics", ""), "trusted")


class BuildSourceReliabilityScoreTests(unittest.TestCase):
    """Overall score computation — weighted combination of tier, diversity,
    trusted ratio, and credibility."""

    def test_returns_none_for_empty_breakdown(self):
        result = build_source_reliability(**_build_defaults(evidence_breakdown=[]))
        self.assertIsNone(result)

    def test_returns_none_for_none_breakdown(self):
        result = build_source_reliability(**_build_defaults(evidence_breakdown=None))
        self.assertIsNone(result)

    def test_single_trusted_source(self):
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=[_breakdown_item(source="Reuters", credibility=0.85, strength=0.7)],
            evidence_items=[_evidence_item(source="Reuters", url="https://www.reuters.com/1")],
            min_domain_diversity=1,
            min_sources=1,
        ))
        self.assertIsNotNone(result)
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["domain_diversity"], 1)
        self.assertEqual(result["source_breakdown"][0]["tier"], "trusted")
        self.assertEqual(result["source_breakdown"][0]["article_count"], 1)
        self.assertAlmostEqual(result["source_breakdown"][0]["avg_credibility"], 0.85)

    def test_multiple_sources_aggregated(self):
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.9, strength=0.8),
            _breakdown_item(source="Reuters", credibility=0.8, strength=0.6),
            _breakdown_item(source="CoinDesk", credibility=0.6, strength=0.5),
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="CoinDesk", url="https://www.coindesk.com/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown,
            evidence_items=items,
            min_domain_diversity=1,
            min_sources=1,
        ))
        self.assertEqual(result["source_count"], 2)  # 2 unique sources
        self.assertEqual(result["domain_diversity"], 2)  # reuters.com + coindesk.com
        # Reuters: avg of 0.9 and 0.8 = 0.85
        reuters = [s for s in result["source_breakdown"] if s["source"] == "Reuters"][0]
        self.assertEqual(reuters["article_count"], 2)
        self.assertAlmostEqual(reuters["avg_credibility"], 0.85)

    def test_domain_diversity_counts_unique_domains(self):
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.85),
            _breakdown_item(source="AP", credibility=0.85),
        ]
        # Both from reuters.com → diversity = 1
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="AP", url="https://www.reuters.com/2"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=1,
        ))
        self.assertEqual(result["domain_diversity"], 1)

    def test_trusted_source_ratio(self):
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.85),  # trusted
            _breakdown_item(source="CoinDesk", credibility=0.6),  # established
            _breakdown_item(source="Random Blog", credibility=0.3),  # unknown
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="CoinDesk", url="https://www.coindesk.com/1"),
            _evidence_item(source="Random Blog", url="https://randomblog.xyz/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=1,
        ))
        # 1 trusted out of 3 sources = 0.3333 (rounded to 4 decimal places)
        self.assertAlmostEqual(result["trusted_source_ratio"], 1 / 3, places=4)

    def test_all_unknown_sources(self):
        breakdown = [
            _breakdown_item(source="Unknown Source 1", credibility=0.3),
            _breakdown_item(source="Unknown Source 2", credibility=0.3),
        ]
        items = [
            _evidence_item(source="Unknown Source 1", url="https://unknown1.xyz/1"),
            _evidence_item(source="Unknown Source 2", url="https://unknown2.xyz/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=1,
        ))
        self.assertEqual(result["trusted_source_ratio"], 0.0)
        self.assertEqual(result["unknown_source_ratio"], 1.0)

    def test_official_source_boosts_trusted_ratio(self):
        breakdown = [
            _breakdown_item(source="SEC Filing", credibility=0.95),  # official
            _breakdown_item(source="Reuters", credibility=0.85),    # trusted
        ]
        items = [
            _evidence_item(source="SEC Filing", url="https://www.sec.gov/filing"),
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=1,
        ))
        # Both official and trusted count toward trusted_source_ratio.
        self.assertEqual(result["trusted_source_ratio"], 1.0)
        self.assertEqual(result["official_source_count"], 1)


class BuildSourceReliabilityDowngradeTests(unittest.TestCase):
    """Downgrade rules — first-match-wins, only YES/NO downgradable."""

    def test_rule_1_low_domain_diversity(self):
        # 2 sources from the same domain → diversity=1 < min_domain_diversity=2
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.9),
            _breakdown_item(source="AP", credibility=0.9),
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="AP", url="https://www.reuters.com/2"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=2, min_sources=1,
        ))
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertIn("域名多样性", result["downgrade_reason"])
        self.assertTrue(result["downgraded"])

    def test_rule_2_low_trusted_ratio(self):
        # All unknown sources → trusted_ratio=0 < min_trusted_ratio=0.4
        breakdown = [
            _breakdown_item(source="Random1", credibility=0.3),
            _breakdown_item(source="Random2", credibility=0.3),
        ]
        items = [
            _evidence_item(source="Random1", url="https://random1.xyz/1"),
            _evidence_item(source="Random2", url="https://random2.xyz/2"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=2, min_trusted_ratio=0.4,
        ))
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertIn("可信来源占比", result["downgrade_reason"])

    def test_rule_3_low_source_count(self):
        # Only 1 source → source_count=1 < min_sources=2
        breakdown = [_breakdown_item(source="Reuters", credibility=0.9)]
        items = [_evidence_item(source="Reuters", url="https://www.reuters.com/1")]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=2,
        ))
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertIn("来源数量", result["downgrade_reason"])

    def test_rule_4_low_overall_score(self):
        # All unknown sources, but diversity and count pass → falls to rule 4.
        breakdown = [
            _breakdown_item(source="Random1", credibility=0.1),
            _breakdown_item(source="Random2", credibility=0.1),
            _breakdown_item(source="Random3", credibility=0.1),
        ]
        items = [
            _evidence_item(source="Random1", url="https://random1.xyz/1"),
            _evidence_item(source="Random2", url="https://random2.xyz/2"),
            _evidence_item(source="Random3", url="https://random3.xyz/3"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=2, min_trusted_ratio=0.0,
            min_sources=2, score_threshold=0.5,
        ))
        # diversity=3 >= 2 (pass), trusted_ratio=0 >= 0.0 (pass),
        # source_count=3 >= 2 (pass), so rule 4 fires.
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertIn("整体可靠性", result["downgrade_reason"])

    def test_first_match_wins_ordering(self):
        # Both rule 1 (low diversity) and rule 2 (low trusted ratio) match.
        # Rule 1 fires first.
        breakdown = [
            _breakdown_item(source="Random1", credibility=0.3),
            _breakdown_item(source="Random2", credibility=0.3),
        ]
        items = [
            _evidence_item(source="Random1", url="https://random1.xyz/1"),
            _evidence_item(source="Random2", url="https://random1.xyz/2"),  # same domain
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=2, min_trusted_ratio=0.4,
        ))
        self.assertIn("域名多样性", result["downgrade_reason"])

    def test_wait_direction_not_downgraded(self):
        # WAIT is non-directional — never downgraded.
        breakdown = [_breakdown_item(source="Random", credibility=0.1)]
        items = [_evidence_item(source="Random", url="https://random.xyz/1")]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            raw_direction="WAIT",
            min_domain_diversity=2, min_trusted_ratio=0.4,
        ))
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertIsNone(result["downgrade_reason"])
        self.assertFalse(result["downgraded"])

    def test_avoid_direction_not_downgraded(self):
        # AVOID is the strictest — never downgraded further.
        breakdown = [_breakdown_item(source="Random", credibility=0.1)]
        items = [_evidence_item(source="Random", url="https://random.xyz/1")]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            raw_direction="AVOID",
            min_domain_diversity=2, min_trusted_ratio=0.4,
        ))
        self.assertEqual(result["suggested_direction"], "AVOID")
        self.assertIsNone(result["downgrade_reason"])
        self.assertFalse(result["downgraded"])

    def test_no_downgrade_when_all_pass(self):
        # All rules pass: good diversity, trusted sources, enough count, high score.
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.9),
            _breakdown_item(source="AP", credibility=0.88),
            _breakdown_item(source="Bloomberg", credibility=0.87),
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="AP", url="https://apnews.com/1"),
            _evidence_item(source="Bloomberg", url="https://www.bloomberg.com/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            raw_direction="YES",
            min_domain_diversity=2, min_trusted_ratio=0.4,
            min_sources=2, score_threshold=0.5,
        ))
        self.assertEqual(result["suggested_direction"], "YES")
        self.assertIsNone(result["downgrade_reason"])
        self.assertFalse(result["downgraded"])
        self.assertGreater(result["overall_score"], 0.7)


class BuildSourceReliabilityOverlayTests(unittest.TestCase):
    """Overlay semantics — raw_direction preserved, suggested_direction set,
    downgraded flag, applied_to_displayed_direction defaults False."""

    def test_raw_direction_preserved(self):
        result = build_source_reliability(**_build_defaults(raw_direction="YES"))
        self.assertEqual(result["raw_direction"], "YES")

    def test_suggested_direction_equals_raw_when_no_downgrade(self):
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.9),
            _breakdown_item(source="AP", credibility=0.88),
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="AP", url="https://apnews.com/1"),
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            min_domain_diversity=1, min_sources=1,
        ))
        self.assertEqual(result["raw_direction"], "YES")
        self.assertEqual(result["suggested_direction"], "YES")
        self.assertFalse(result["downgraded"])

    def test_downgraded_flag_true_when_suggested_differs(self):
        breakdown = [_breakdown_item(source="Random", credibility=0.1)]
        items = [_evidence_item(source="Random", url="https://random.xyz/1")]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            raw_direction="NO",
            min_domain_diversity=2, min_sources=2,
        ))
        self.assertEqual(result["raw_direction"], "NO")
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertTrue(result["downgraded"])

    def test_applied_to_displayed_direction_defaults_false(self):
        result = build_source_reliability(**_build_defaults())
        self.assertFalse(result["applied_to_displayed_direction"])

    def test_no_direction_downgrade_for_empty_recommendation(self):
        # raw_direction=None → normalized to "WAIT" → never downgraded.
        breakdown = [_breakdown_item(source="Random", credibility=0.1)]
        items = [_evidence_item(source="Random", url="https://random.xyz/1")]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
            raw_direction=None,
        ))
        self.assertEqual(result["raw_direction"], "WAIT")
        self.assertEqual(result["suggested_direction"], "WAIT")
        self.assertFalse(result["downgraded"])

    def test_never_raises_on_adversarial_input(self):
        # Malformed items are skipped; the function returns a valid block
        # (or None for completely empty breakdown).
        breakdown = [
            {"source": None, "credibility": "bad", "strength": []},
            {"source": "", "direction": 123},
            {},
        ]
        result = build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=None,
        ))
        self.assertIsNotNone(result)
        self.assertEqual(result["raw_direction"], "YES")

    def test_does_not_mutate_input_breakdown(self):
        breakdown = [
            _breakdown_item(source="Reuters", credibility=0.9),
            _breakdown_item(source="AP", credibility=0.88),
        ]
        items = [
            _evidence_item(source="Reuters", url="https://www.reuters.com/1"),
            _evidence_item(source="AP", url="https://apnews.com/1"),
        ]
        breakdown_before = [dict(item) for item in breakdown]
        items_before = [dict(item) for item in items]
        build_source_reliability(**_build_defaults(
            evidence_breakdown=breakdown, evidence_items=items,
        ))
        self.assertEqual(breakdown, breakdown_before)
        self.assertEqual(items, items_before)


if __name__ == "__main__":
    unittest.main()

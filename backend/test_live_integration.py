#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Live integration test for Event Intelligence Platform core services.
Tests the full pipeline without HTTP layer.
"""
import asyncio
import sys
import os
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.event_intelligence_service import analyze_event_question


async def test_event_analysis():
    """Test live event analysis with real LLM call."""
    print("=" * 60)
    print("LIVE INTEGRATION TEST: Event Analysis")
    print("=" * 60)
    print()

    # Simple test case
    event_question = "Will Bitcoin reach $100,000 by end of 2026?"
    baseline = 45.0
    context = "Recent institutional adoption increasing. Major ETF inflows."

    print(f"Event Question: {event_question}")
    print(f"Baseline Probability: {baseline}%")
    print(f"News Context: {context}")
    print()
    print("Calling LLM and analyzing evidence...")
    print()

    try:
        result = await analyze_event_question(
            event_question=event_question,
            baseline_probability=baseline,
            news_context=context
        )

        print("✅ SUCCESS - Event analysis completed!")
        print()
        print("-" * 60)
        print("RESULTS:")
        print("-" * 60)
        print(f"Event ID: {result['event_id']}")
        print(f"Event Title: {result['event_title']}")
        print()
        print("Probability Assessment:")
        print(f"  Baseline:  {result['probability']['baseline']}%")
        print(f"  Estimated: {result['probability']['estimated']}%")
        print(f"  Change:    {result['probability']['change']:+.1f}%")
        print(f"  Direction: {result['probability']['direction']}")
        print()
        print("Credibility:")
        print(f"  Score: {result['credibility']['score']}/100")
        print(f"  Level: {result['credibility']['level']}")
        print()
        print("Impact:")
        print(f"  Score: {result['impact']['score']}/100")
        print(f"  Level: {result['impact']['level']}")
        print()
        print("Intelligence Report:")
        print(f"  Headline: {result['intelligence_report']['headline']}")
        print(f"  Recommended Action: {result['intelligence_report']['recommended_action']}")
        print()
        print("Value Score: {}/100".format(result['value_score']))
        print("-" * 60)

        return True

    except Exception as e:
        print(f"❌ FAILED - Error during analysis:")
        print(f"  {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_event_analysis())
    sys.exit(0 if success else 1)

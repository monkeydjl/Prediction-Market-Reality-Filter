import asyncio
import hashlib
import itertools
import logging
import math
from typing import Any

from app.core.config import settings
from app.services.translation_service import translate_articles
from app.utils.market_utils import safe_float
from app.utils.helpers import clamp01


logger = logging.getLogger(__name__)

# discover analyzes up to limit * this many candidates across all event sources;
# the cap bounds analyze_event / LLM calls per scan as sources are added.
_CANDIDATE_POOL_FACTOR = 3


def build_event_record(
    analysis: dict[str, Any],
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert legacy market analysis into an event intelligence record."""
    source_info = _source_info(source)
    risk_flags = analysis.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = []
    risk_level = analysis.get("risk_level")
    if not isinstance(risk_level, str) or not risk_level.strip():
        risk_level = "UNKNOWN"
    evidence_direction = analysis.get("evidence_direction")
    if not isinstance(evidence_direction, str) or not evidence_direction.strip():
        evidence_direction = "neutral"
    question = str(
        analysis.get("event_question")
        or analysis.get("market_question")
        or ""
    ).strip()
    baseline = safe_float(analysis.get("market_probability"), 50.0)
    estimated_value = analysis.get("ai_probability")
    if estimated_value is None:
        estimated_value = analysis.get("final_probability")
    estimated = safe_float(estimated_value, baseline)
    change = round(estimated - baseline, 2)
    confidence = clamp01(analysis.get("confidence_score"))
    news_quality = clamp01(analysis.get("news_quality_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    source_count = max(0, int(safe_float(analysis.get("source_count"), 0)))
    trust_score = calculate_trust_score(analysis)
    impact_score = calculate_impact_score(analysis)

    return {
        "event_id": _event_id(question),
        "event_title": question,
        "event_title_zh": str(analysis.get("title_zh") or "").strip()[:300],
        "event_summary": _summary(analysis),
        "probability": {
            "baseline": round(baseline, 2),
            "estimated": round(estimated, 2),
            "change": change,
            "direction": probability_direction(change),
        },
        "credibility": {
            "score": trust_score,
            "level": score_level(trust_score),
            "confidence": round(confidence, 3),
            "news_quality": round(news_quality, 3),
            "evidence_strength": round(evidence_strength, 3),
            "source_count": source_count,
        },
        "impact": {
            "score": impact_score,
            "level": score_level(impact_score),
            "drivers": impact_drivers(analysis),
        },
        "risk": {
            "level": risk_level,
            "flags": risk_flags,
        },
        "evidence": {
            "direction": evidence_direction,
            "strength": round(evidence_strength, 3),
            "conflict": round(clamp01(analysis.get("evidence_conflict_score")), 3),
            "freshness": round(clamp01(analysis.get("freshness_score")), 3),
            "resolution_relevance": round(
                clamp01(analysis.get("resolution_relevance_score")), 3
            ),
        },
        "source": source_info,
        "value_score": calculate_value_score(impact_score, trust_score),
        "tracking": _default_tracking(impact_score),
        "intelligence_report": {
            "headline": build_headline(
                str(analysis.get("title_zh") or "").strip() or question,
                change,
                trust_score,
                impact_score,
            ),
            "why_it_matters": build_why_it_matters(analysis, change),
            "probability_assessment": build_probability_assessment(
                baseline,
                estimated,
                trust_score,
            ),
            "recommended_action": recommended_action(trust_score, impact_score, change),
        },
        # legacy_analysis carries the full legacy market-analysis dict
        # (signal, position_size, expected_edge, ...) retained verbatim for
        # backward compatibility with /scan and /trades. The event layer does
        # NOT read it for its own logic; the event-facing fields above
        # (probability, credibility, impact, evidence, intelligence_report) are
        # the canonical surface.
        "legacy_analysis": analysis,
        "semantics": _build_semantics(analysis),
    }


async def analyze_event(
    event_question: str,
    baseline_probability: float = 50.0,
    news_context: str = "",
    source: dict[str, Any] | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
) -> dict[str, Any]:
    from app.services.ai_analysis_service import analyze_market
    from app.services.cross_validation_service import credibility_delta, cross_validate

    analysis = await analyze_market(
        market_question=event_question,
        market_probability=baseline_probability,
        news_context=news_context,
        volume=volume,
        liquidity=liquidity,
    )
    record = build_event_record(analysis, source=source)
    cross = await cross_validate(
        question=event_question,
        news_context=news_context,
        primary_probability=record["probability"]["estimated"],
    )
    if cross is not None:
        record["cross_validation"] = cross
        credibility = record["credibility"]
        adjusted = max(0, min(100, credibility["score"] + credibility_delta(cross["agreement"])))
        credibility["score"] = adjusted
        credibility["level"] = score_level(adjusted)
    _apply_calibration_feedback(record, analysis, cross)
    return record


def _apply_calibration_feedback(
    record: dict[str, Any],
    analysis: dict[str, Any],
    cross: dict[str, Any] | None,
) -> None:
    """Record the probability signals and, when enabled, fold calibration
    history back into the published estimate.

    The component probabilities (market baseline, anchored LLM estimate, and the
    cross-validation model when present) are ALWAYS recorded under
    `calibration_components`, so a per-component Brier history can accumulate as
    events resolve - this is the data the feedback loop later weights by, and it
    must be captured even while the loop is off.

    When settings.CALIBRATION_FEEDBACK_ENABLED is on, the recorded signals are
    fused (weighted by each component's Brier history) and shrunk toward the
    base-rate prior (by the category's Brier history), and the result overwrites
    the published probability. Until enough outcomes have accumulated this is a
    no-op (the adjusted value equals the LLM estimate), so the default-off and
    early-on behavior is identical to today's single-LLM estimate.
    """
    probability = record["probability"]
    components = {
        "market": probability["baseline"],
        "llm": probability["estimated"],
    }
    if cross is not None and _looks_numeric(cross.get("probability")):
        components["cross_validation"] = float(cross["probability"])
    record["calibration_components"] = components

    if not settings.CALIBRATION_FEEDBACK_ENABLED:
        return

    from app.services.calibration_feedback_service import adjust_probability

    category = str(analysis.get("base_rate_category") or "unknown")
    prior = safe_float(analysis.get("base_rate_prior"), probability["baseline"])
    adjusted, info = adjust_probability(components, category, prior)

    baseline = probability["baseline"]
    probability["estimated"] = adjusted
    probability["change"] = round(adjusted - baseline, 2)
    probability["direction"] = probability_direction(probability["change"])
    record["calibration_feedback"] = info
    record["intelligence_report"]["probability_assessment"] = (
        build_probability_assessment(
            baseline, adjusted, record["credibility"]["score"]
        )
    )


async def analyze_event_question(
    event_question: str,
    baseline_probability: float = 50.0,
    news_context: str | None = None,
    volume: float | None = None,
    liquidity: float | None = None,
) -> dict[str, Any]:
    if news_context is not None:
        record = await analyze_event(
            event_question=event_question,
            baseline_probability=baseline_probability,
            news_context=news_context,
            source={"type": "manual"},
            volume=volume,
            liquidity=liquidity,
        )
    else:
        filtered_news = await _build_filtered_news(event_question)
        record = await analyze_event(
            event_question=event_question,
            baseline_probability=baseline_probability,
            news_context=filtered_news["context"],
            source={"type": "manual"},
            volume=volume,
            liquidity=liquidity,
        )
        record["news_filter"] = filtered_news["summary"]
        articles = await translate_articles(filtered_news.get("articles") or [])
        record["evidence_items"] = build_evidence_items(articles)

    _persist_events([record])
    return record


async def _collect_candidate_events(
    limit: int,
    shared_articles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fetch candidate events from every event source concurrently, isolating a
    failing source so one down source does not break discovery.

    Market sources produce candidates from prices; the open-web source extracts
    them from `shared_articles` (the same feed articles used for evidence), so an
    article can become an event subject, not just evidence. Each source is asked
    for `limit` candidates. The per-source lists are round-robin interleaved and
    the merged pool is capped at ``limit * _CANDIDATE_POOL_FACTOR``. Interleaving
    keeps every source represented under the cap (a plain concatenation would let
    the first source fill the whole budget); the cap bounds how many analyze_event
    / LLM calls a scan makes as sources are added. discover_events still ranks the
    pool by value_score and returns the top ``limit``.
    """
    from app.services.event_extraction_service import extract_candidate_events
    from app.services.kalshi_event_source import (
        fetch_candidate_events as fetch_kalshi_events,
    )
    from app.services.manifold_event_source import (
        fetch_candidate_events as fetch_manifold_events,
    )
    from app.services.polymarket_event_source import (
        fetch_candidate_events as fetch_polymarket_events,
        fetch_crypto_candidate_events as fetch_polymarket_crypto_events,
    )

    market_sources: list[tuple[str, Any]] = [
        ("Polymarket", fetch_polymarket_events),
        ("Manifold", fetch_manifold_events),
        ("Kalshi", fetch_kalshi_events),
    ]
    # Opt-in crypto-only Polymarket fetch. The default Polymarket fetch ranks by
    # volume, so geopolitics crowds crypto out of the top-N; this adds a
    # crypto-only fetch as an extra candidate source so crypto markets reach the
    # pool. Dedupe keeps cross-source duplicates out (a crypto market surfacing
    # in both the default and the crypto-only fetch is analyzed once).
    if settings.POLYMARKET_CRYPTO_FETCH_ENABLED:
        market_sources.append(("Polymarket Crypto", fetch_polymarket_crypto_events))
    labels = [name for name, _ in market_sources] + ["Open Web"]
    results = await asyncio.gather(
        *(fetch(limit) for _, fetch in market_sources),
        extract_candidate_events(shared_articles or [], limit),
        return_exceptions=True,
    )
    per_source: list[list[dict[str, Any]]] = []
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.warning("Event source failed [%s]: %s", label, result)
            continue
        per_source.append(result)

    # Round-robin across sources so the cap keeps every source represented.
    merged = [
        candidate
        for tier in itertools.zip_longest(*per_source)
        for candidate in tier
        if candidate is not None
    ]
    # Drop cross-source duplicates before the cap, so the same real-world event
    # surfacing from multiple sources is analyzed once (higher-priority source
    # kept). Runs before analysis, saving LLM calls.
    from app.services.candidate_dedup_service import dedupe_candidates

    deduped = dedupe_candidates(merged)
    return deduped[: limit * _CANDIDATE_POOL_FACTOR]


async def discover_events(
    limit: int = 10,
    use_cache: bool = True,
) -> dict[str, Any]:
    from app.memory.event_cache import get_cached_event, set_cached_event
    from app.services.event_collection_service import collect_shared_articles

    # Query-independent feeds are fetched once per scan and reused twice: as
    # open-web event candidates (extraction) and as shared evidence for every
    # candidate below.
    shared_articles = await collect_shared_articles()
    candidate_events = await _collect_candidate_events(
        limit, shared_articles=shared_articles
    )
    semaphore = asyncio.Semaphore(getattr(settings, "LLM_CONCURRENCY", 4))

    async def process_event(
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        """Analyze one candidate. Returns (record, is_new).

        is_new is False when the record came from the per-question cache; such
        records are still returned for the response but must NOT be re-audited,
        because their probability snapshot is unchanged and re-auditing would
        append duplicate snapshots that pollute trend analysis and grow the
        audit log without bound.
        """
        async with semaphore:
            try:
                question = str(candidate.get("question") or "").strip()
                if not question:
                    return None

                if use_cache:
                    cached = get_cached_event(question)
                    if cached is not None:
                        return cached, False

                filtered_news = await _build_filtered_news(
                    question, shared_articles=shared_articles
                )
                if filtered_news["summary"]["selected_count"] == 0:
                    return None

                record = await analyze_event(
                    event_question=question,
                    baseline_probability=safe_float(
                        candidate.get("baseline_probability"), 50.0
                    ),
                    news_context=filtered_news["context"],
                    source=candidate.get("source"),
                    volume=candidate.get("volume"),
                    liquidity=candidate.get("liquidity"),
                )
                record["news_filter"] = filtered_news["summary"]
                articles = await translate_articles(filtered_news.get("articles") or [])
                record["evidence_items"] = build_evidence_items(articles)
                if use_cache:
                    set_cached_event(question, record)
                return record, True
            except Exception as exc:
                logger.warning(
                    "Event discovery failed [%s]: %s",
                    str(candidate.get("question", ""))[:80],
                    exc,
                )
                return None

    raw = await asyncio.gather(
        *(process_event(candidate) for candidate in candidate_events)
    )
    results = [item for item in raw if item is not None]
    events = [record for record, _ in results]
    events.sort(key=lambda item: item.get("value_score", 0), reverse=True)
    # Persist / audit only freshly-analyzed records. Cached records already
    # have their snapshot; re-auditing them would append duplicate snapshots.
    fresh = [record for record, is_new in results if is_new]
    _persist_events(fresh)
    return {
        "platform": "Event Intelligence Platform",
        "source": "Multi-source event discovery",
        "count": len(events[:limit]),
        "events": events[:limit],
    }


def _persist_events(records: list[dict[str, Any]]) -> None:
    """Durable persistence + audit + prediction freeze for event records.

    Each stage has its own error boundary so a failure in one does not silently
    swallow the others (a single shared try/except previously meant a freeze
    error could leave events saved but predictions missing, with only a generic
    warning):

    - save_events is the gate: if the durable store write fails, abort (audit and
      freeze would reference unsaved events), logging the failure explicitly.
    - record_event (audit) failures are isolated per event and never block the
      freeze - the audit log is observability, not the loop's source of truth.
    - freeze_prediction failures are isolated per event and logged with the
      event_id + reason, so a missing prediction is visible, not hidden.
    """
    if not records:
        return
    from app.memory.event_store import save_events
    from app.memory.prediction_store import freeze_prediction
    from app.services.event_audit_service import record_event

    try:
        save_events(records)
    except Exception as exc:
        # The store write is the foundation; without it audit/freeze would
        # reference unsaved events. Abort the batch and surface it.
        logger.error("Event store write failed, skipping audit/freeze: %s", exc)
        return

    for record in records:
        event_id = record.get("event_id")
        try:
            record_event(record)
        except Exception as exc:
            logger.warning("Event audit failed for %s: %s", event_id, exc)
        try:
            # Freeze a committed prediction for market-derived events. Idempotent
            # and market-gated inside the store, so re-scans and news events are
            # safe no-ops (no market price -> no edge -> no prediction).
            freeze_prediction(record)
        except Exception as exc:
            logger.warning("Prediction freeze failed for %s: %s", event_id, exc)


async def _build_filtered_news(
    event_question: str,
    shared_articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.services.event_collection_service import collect_articles
    from app.services.market_semantics_service import parse_market_semantics
    from app.services.news_filter_service import filter_news_for_market
    from app.services.semantic_relevance_service import annotate_semantic_relevance

    articles = await collect_articles(event_question, shared_articles=shared_articles)
    # Opt-in semantic relevance (no-op unless EMBEDDING_MODEL is configured);
    # filter_news_for_market blends it with keyword relevance. Pass the parsed
    # semantics so the embedding query is enriched with the event's entities and
    # resolution conditions - critical for price-threshold questions (e.g. crypto
    # "reach $2,000") whose relevant news shares little surface vocabulary with
    # the question and would otherwise be dropped by keyword relevance alone.
    semantics = parse_market_semantics(event_question)
    await annotate_semantic_relevance(event_question, articles, semantics)
    return filter_news_for_market(
        market_question=event_question,
        articles=articles,
    )


def _priority_from_score(score: int) -> str:
    return {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(
        score_level(score), "medium"
    )


def _default_tracking(impact_score: int) -> dict[str, str]:
    """Default human-tracking decision for a freshly analyzed event.

    status starts at "watching"; priority seeds from impact level. A user's
    explicit choice is preserved across re-scans by event_store.save_events.
    """
    return {"status": "watching", "priority": _priority_from_score(impact_score)}


def build_evidence_items(articles: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per-item evidence rows for the detail UI, from the filtered news articles.

    Each article already carries kind (official/news), source, url, recency and
    quality/relevance scores from the news filter. The backend computes evidence
    direction only in aggregate (there is no per-item stance), so each item
    exposes quality / relevance rather than a fabricated supports/contradicts.
    """
    items: list[dict[str, Any]] = []
    for article in articles or []:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        item = {
            "kind": article.get("kind") or "news",
            "source": str(article.get("source") or "").strip(),
            "title": title[:300],
            "summary": str(article.get("description") or "").strip()[:500],
            "url": str(article.get("url") or "").strip(),
            "published": str(article.get("published") or "").strip(),
            "quality": round(clamp01(article.get("quality_score")), 3),
            "relevance": round(clamp01(article.get("relevance_score")), 3),
        }
        # Chinese translations (added by translation_service.translate_articles
        # during discovery) carry through when present; the UI shows zh with the
        # English original as fallback. Absent for untranslated/manual flows.
        title_zh = str(article.get("title_zh") or "").strip()
        if title_zh:
            item["title_zh"] = title_zh[:300]
        summary_zh = str(article.get("summary_zh") or "").strip()
        if summary_zh:
            item["summary_zh"] = summary_zh[:500]
        items.append(item)
    return items


def calculate_trust_score(analysis: dict[str, Any]) -> int:
    confidence = clamp01(analysis.get("confidence_score"))
    news_quality = clamp01(analysis.get("news_quality_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    relevance = clamp01(analysis.get("resolution_relevance_score"))
    conflict_penalty = 1.0 - clamp01(analysis.get("evidence_conflict_score"))
    score = (
        confidence * 30
        + news_quality * 25
        + evidence_strength * 20
        + relevance * 15
        + conflict_penalty * 10
    )
    return int(round(max(0, min(100, score))))


def calculate_impact_score(analysis: dict[str, Any]) -> int:
    probability_change = min(
        abs(safe_float(analysis.get("divergence"), 0.0)),
        40.0,
    ) / 40.0
    confidence = clamp01(analysis.get("confidence_score"))
    evidence_strength = clamp01(analysis.get("evidence_strength"))
    relevance = clamp01(analysis.get("resolution_relevance_score"))
    score = (
        probability_change * 45
        + confidence * 20
        + evidence_strength * 20
        + relevance * 15
    )
    return int(round(max(0, min(100, score))))


def calculate_value_score(impact_score: int, trust_score: int) -> int:
    """Linear value score: impact weighted by trust.

    trust=100 -> value = impact  (full trust, full value)
    trust=0   -> value = 0       (zero trust, zero value)

    The old formula ``impact * (0.5 + trust/200)`` kept half the impact even
    when trust was zero, which overstated low-trust events by up to 50 points.
    """
    return int(round(impact_score * trust_score / 100))


def probability_direction(change: float) -> str:
    if change >= 2:
        return "rising"
    if change <= -2:
        return "falling"
    return "stable"


def score_level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def impact_drivers(analysis: dict[str, Any]) -> list[str]:
    drivers = []
    if abs(safe_float(analysis.get("divergence"), 0.0)) >= 10:
        drivers.append("material_probability_change")
    if clamp01(analysis.get("evidence_strength")) >= 0.35:
        drivers.append("strong_evidence")
    if clamp01(analysis.get("resolution_relevance_score")) >= 0.35:
        drivers.append("direct_resolution_relevance")
    base_rate_category = analysis.get("base_rate_category")
    if (
        isinstance(base_rate_category, str)
        and base_rate_category
        and base_rate_category != "unknown"
    ):
        drivers.append(f"base_rate:{base_rate_category}")
    return drivers or ["monitor_for_confirmation"]


_DIRECTION_ZH = {"rising": "上行", "falling": "下行", "stable": "持平"}
_LEVEL_ZH = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}


def build_headline(
    question: str,
    change: float,
    trust_score: int,
    impact_score: int,
) -> str:
    direction = _DIRECTION_ZH.get(probability_direction(change), "持平")
    return (
        f"{direction}概率信号：「{question[:90]}」"
        f"（可信度 {trust_score}/100，影响 {impact_score}/100）"
    )


def build_why_it_matters(analysis: dict[str, Any], change: float) -> str:
    narrative = str(analysis.get("narrative_summary") or "").strip()
    if narrative:
        return narrative[:500]
    return (
        "该事件值得关注：现有证据显示其发生概率相对当前基准"
        f"移动了约 {abs(change):.1f} 个百分点。"
    )


def build_probability_assessment(
    baseline: float,
    estimated: float,
    trust_score: int,
) -> str:
    return (
        f"基准概率 {baseline:.1f}% 变化至 {estimated:.1f}%，"
        f"可信度{_LEVEL_ZH.get(score_level(trust_score), '中')}。"
    )


def recommended_action(trust_score: int, impact_score: int, change: float) -> str:
    if trust_score >= 70 and impact_score >= 60:
        return "建议人工复核，并持续关注后续证据。"
    if trust_score >= 45 and abs(change) >= 5:
        return "作为活跃情报项跟踪，等待进一步确认。"
    return "保持观察；当前证据强度不足以升级处理。"


def _summary(analysis: dict[str, Any]) -> str:
    return str(
        analysis.get("narrative_summary")
        or analysis.get("reasoning")
        or "暂无摘要。"
    )[:500]


def _source_info(source: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {"type": "manual"}
    clean = {
        key: value
        for key, value in source.items()
        if (
            isinstance(value, (int, float, bool))
            or (isinstance(value, str) and value.strip())
        )
    }
    return clean or {"type": "manual"}


def _build_semantics(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Build the EventSemantics dict from analysis, or None when empty.

    Populated from the LLM analysis output (resolution_criteria, time_horizon,
    entities). Returns None when all three are empty/blank so records without
    structured semantics stay small and the field's absence is meaningful.
    """
    resolution_criteria = str(analysis.get("resolution_criteria") or "").strip()
    time_horizon = str(analysis.get("time_horizon") or "").strip()
    entities_raw = analysis.get("entities")
    entities = (
        [str(item).strip() for item in entities_raw if str(item or "").strip()]
        if isinstance(entities_raw, list)
        else []
    )
    if not resolution_criteria and not time_horizon and not entities:
        return None
    return {
        "resolution_criteria": resolution_criteria,
        "time_horizon": time_horizon,
        "entities": entities,
    }


def _event_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _looks_numeric(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False

"""Three-layer sport market link matching evaluation (P1-SB1).

Evaluates the rule / LLM / manual matchers in
``SportMarketBridgeService`` against a labeled JSONL dataset and reports
precision / recall / F1 per matcher and overall.

Dataset schema (one JSON object per line):

    {
      "case_id": "rule_full_match_wc",
      "match_id": "wc-2026-06-13-ARG-FRA",
      "market": {
        "contract_id": "poly_001",
        "source": "polymarket",
        "market_question": "Will Argentina win the 2026 World Cup Final?",
        "outcome_label": "Yes",
        "detected_sport": "football",
        "detected_competition": "wc",
        "detected_teams": ["Argentina", "France"],
        "detected_date": "2026-06-13"
      },
      "expected": {
        "mapped_outcome": "home_win",
        "confidence_min": 0.9,
        "matcher_accept": ["rule"]
      },
      "notes": "optional context"
    }

Usage:
    python -m scripts.eval_sport_market_matching \\
        --dataset data/eval/sport_market_link_eval.sample.jsonl \\
        --matcher rule \\
        --report text

    python -m scripts.eval_sport_market_matching \\
        --dataset data/eval/sport_market_link_eval.sample.jsonl \\
        --matcher all \\
        --manual-overrides data/eval/sport_market_link_eval.manual.jsonl \\
        --report json --out eval_report.json

The script does NOT modify any link store. It only invokes the matcher
functions (rule / llm) in-memory or reads manual override labels from a
side JSONL file. LLM calls go through the standard LLM gateway — set
``LLM_STARTUP_CHECK_ENABLED=false`` to suppress live checks.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


@dataclass
class EvalCase:
    case_id: str
    match_id: str
    market: dict[str, Any]
    expected: dict[str, Any]
    notes: str = ""


@dataclass
class CaseResult:
    case_id: str
    matcher: str
    predicted_accept: bool
    true_accept: bool
    outcome_ok: bool
    confidence_ok: bool
    predicted_outcome: str | None
    predicted_confidence: float | None
    detail: str = ""


@dataclass
class MatcherSummary:
    matcher: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def load_dataset(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid JSON: {exc}") from exc
            cases.append(
                EvalCase(
                    case_id=obj["case_id"],
                    match_id=obj["match_id"],
                    market=obj["market"],
                    expected=obj["expected"],
                    notes=obj.get("notes", ""),
                )
            )
    return cases


def load_manual_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid JSON: {exc}") from exc
            overrides[obj["case_id"]] = obj
    return overrides


def _evaluate_match(
    matcher: str,
    case: EvalCase,
    *,
    rule_result: Any | None,
    llm_result: Any | None,
    manual_overrides: dict[str, dict[str, Any]],
) -> CaseResult:
    expected_outcome = case.expected.get("mapped_outcome", "none")
    confidence_min = float(case.expected.get("confidence_min", 0.0))
    matcher_accept = set(case.expected.get("matcher_accept", []))

    predicted_outcome: str | None = None
    predicted_confidence: float | None = None
    detail = ""

    if matcher == "rule":
        if rule_result is None:
            detail = "rule returned None"
        else:
            predicted_outcome = rule_result.mapped_outcome
            predicted_confidence = rule_result.confidence
            detail = rule_result.reasoning
    elif matcher == "llm":
        if llm_result is None:
            detail = "llm returned None"
        else:
            predicted_outcome = llm_result.mapped_outcome
            predicted_confidence = llm_result.confidence
            detail = llm_result.reasoning
    elif matcher == "manual":
        ov = manual_overrides.get(case.case_id)
        if ov is None:
            detail = "no manual override"
        else:
            predicted_outcome = ov.get("mapped_outcome")
            predicted_confidence = float(ov.get("confidence", 0.0))
            detail = ov.get("reasoning", "")
    else:
        raise ValueError(f"unknown matcher: {matcher}")

    outcome_ok = predicted_outcome == expected_outcome
    confidence_ok = (
        predicted_confidence is not None and predicted_confidence >= confidence_min
    )
    predicted_accept = outcome_ok and confidence_ok and predicted_outcome != "none"
    true_accept = matcher in matcher_accept

    return CaseResult(
        case_id=case.case_id,
        matcher=matcher,
        predicted_accept=predicted_accept,
        true_accept=true_accept,
        outcome_ok=outcome_ok,
        confidence_ok=confidence_ok,
        predicted_outcome=predicted_outcome,
        predicted_confidence=predicted_confidence,
        detail=detail,
    )


def _aggregate(results: list[CaseResult]) -> MatcherSummary:
    summary = MatcherSummary(matcher=results[0].matcher if results else "unknown")
    for r in results:
        if r.predicted_accept and r.true_accept:
            summary.tp += 1
        elif r.predicted_accept and not r.true_accept:
            summary.fp += 1
        elif not r.predicted_accept and r.true_accept:
            summary.fn += 1
        else:
            summary.tn += 1
    return summary


def _build_bridge():
    from app.kernel.sport_market_bridge_service import SportMarketBridgeService

    return SportMarketBridgeService()


def _run_rule(bridge: Any, case: EvalCase) -> Any | None:
    return bridge._rule_match(
        match_id=case.match_id,
        market_question=case.market.get("market_question", ""),
        detected_teams=case.market.get("detected_teams", []),
        detected_competition=case.market.get("detected_competition"),
    )


async def _run_llm(bridge: Any, case: EvalCase) -> Any | None:
    return await bridge._llm_match(
        match_id=case.match_id,
        market_question=case.market.get("market_question", ""),
        detected_competition=case.market.get("detected_competition"),
        detected_teams=case.market.get("detected_teams", []),
    )


def _run_matcher_set(
    cases: list[EvalCase],
    matchers: list[str],
    *,
    manual_overrides: dict[str, dict[str, Any]],
) -> dict[str, list[CaseResult]]:
    bridge = _build_bridge()
    # Pre-compute rule and llm results once per case so each matcher
    # reuses them without re-invoking the LLM.
    rule_cache: dict[str, Any | None] = {}
    llm_cache: dict[str, Any | None] = {}

    if "rule" in matchers:
        for case in cases:
            rule_cache[case.case_id] = _run_rule(bridge, case)
    if "llm" in matchers:
        async def _gather_llm() -> None:
            for case in cases:
                llm_cache[case.case_id] = await _run_llm(bridge, case)
        asyncio.run(_gather_llm())

    per_matcher: dict[str, list[CaseResult]] = {m: [] for m in matchers}
    for matcher in matchers:
        for case in cases:
            per_matcher[matcher].append(
                _evaluate_match(
                    matcher,
                    case,
                    rule_result=rule_cache.get(case.case_id),
                    llm_result=llm_cache.get(case.case_id),
                    manual_overrides=manual_overrides,
                )
            )
    return per_matcher


def _format_text(
    per_matcher: dict[str, list[CaseResult]],
    summaries: dict[str, MatcherSummary],
) -> str:
    lines: list[str] = []
    lines.append("=== Sport Market Link Matching Eval ===")
    for matcher, results in per_matcher.items():
        s = summaries[matcher]
        lines.append(f"\n[{matcher}] n={len(results)}")
        lines.append(
            f"  TP={s.tp} FP={s.fp} FN={s.fn} TN={s.tn}  "
            f"precision={s.precision:.3f} recall={s.recall:.3f} f1={s.f1:.3f}"
        )
        for r in results:
            flag = "OK" if r.predicted_accept == r.true_accept else "MISS"
            lines.append(
                f"  {flag} {r.case_id:<32} pred_out={r.predicted_outcome} "
                f"pred_conf={r.predicted_confidence} accept={r.predicted_accept} "
                f"expected_accept={r.true_accept} | {r.detail}"
            )
    return "\n".join(lines)


def _format_json(
    per_matcher: dict[str, list[CaseResult]],
    summaries: dict[str, MatcherSummary],
) -> dict[str, Any]:
    return {
        "matchers": {
            matcher: {
                "summary": {
                    "tp": s.tp,
                    "fp": s.fp,
                    "fn": s.fn,
                    "tn": s.tn,
                    "precision": s.precision,
                    "recall": s.recall,
                    "f1": s.f1,
                },
                "cases": [asdict(r) for r in results],
            }
            for matcher, (s, results) in (
                (m, (summaries[m], per_matcher[m])) for m in per_matcher
            )
        },
    }


def run_eval(
    cases: list[EvalCase],
    matchers: list[str],
    *,
    manual_overrides: dict[str, dict[str, Any]],
    report_format: str,
) -> tuple[str | dict[str, Any], dict[str, MatcherSummary]]:
    per_matcher = _run_matcher_set(cases, matchers, manual_overrides=manual_overrides)
    summaries = {m: _aggregate(rs) for m, rs in per_matcher.items()}
    if report_format == "json":
        return _format_json(per_matcher, summaries), summaries
    return _format_text(per_matcher, summaries), summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate three-layer sport market link matching (P1-SB1)",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to JSONL labeled dataset (see module docstring for schema)",
    )
    parser.add_argument(
        "--matcher",
        default="all",
        choices=["rule", "llm", "manual", "all"],
        help="Which matcher to evaluate. 'all' = rule + llm + manual",
    )
    parser.add_argument(
        "--manual-overrides",
        default=None,
        help="Path to JSONL manual override file (case_id, mapped_outcome, confidence)",
    )
    parser.add_argument(
        "--report",
        default="text",
        choices=["text", "json"],
        help="Report format",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output file path; default stdout",
    )

    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset).resolve()
    if not dataset_path.exists():
        print(f"[FAIL] dataset not found: {dataset_path}", file=sys.stderr)
        return 2

    cases = load_dataset(dataset_path)
    if not cases:
        print("[FAIL] dataset is empty", file=sys.stderr)
        return 2

    matchers = ["rule", "llm", "manual"] if args.matcher == "all" else [args.matcher]
    manual_overrides = (
        load_manual_overrides(Path(args.manual_overrides).resolve())
        if args.manual_overrides
        else {}
    )

    report, _summaries = run_eval(
        cases,
        matchers,
        manual_overrides=manual_overrides,
        report_format=args.report,
    )

    output_text = (
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.report == "json"
        else report
    )
    if args.out:
        Path(args.out).write_text(output_text, encoding="utf-8")
        print(f"[OK] report written to {args.out}")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(output_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

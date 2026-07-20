"""Tests for the three-layer sport market link matching eval script (P1-SB1)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import eval_sport_market_matching as eval_mod


@dataclass(frozen=True)
class _MockMatchResult:
    confidence: float
    mapped_outcome: str
    reasoning: str


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _make_case(
    *,
    case_id: str,
    match_id: str = "wc-2026-06-13-ARG-FRA",
    expected_outcome: str = "home_win",
    confidence_min: float = 0.9,
    matcher_accept: list[str] | None = None,
    market_question: str = "Will Argentina win?",
    detected_teams: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "match_id": match_id,
        "market": {
            "contract_id": f"poly_{case_id}",
            "source": "polymarket",
            "market_question": market_question,
            "outcome_label": "Yes",
            "detected_sport": "football",
            "detected_competition": "wc",
            "detected_teams": detected_teams if detected_teams is not None else ["Argentina", "France"],
            "detected_date": "2026-06-13",
        },
        "expected": {
            "mapped_outcome": expected_outcome,
            "confidence_min": confidence_min,
            "matcher_accept": matcher_accept or [],
        },
        "notes": "",
    }


def test_load_dataset_parses_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    _write_jsonl(path, [_make_case(case_id="c1"), _make_case(case_id="c2")])
    cases = eval_mod.load_dataset(path)
    assert len(cases) == 2
    assert cases[0].case_id == "c1"
    assert cases[1].market["detected_teams"] == ["Argentina", "France"]


def test_load_dataset_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text(
        json.dumps(_make_case(case_id="c1")) + "\n\n  \n" + json.dumps(_make_case(case_id="c2")) + "\n",
        encoding="utf-8",
    )
    cases = eval_mod.load_dataset(path)
    assert len(cases) == 2


def test_load_dataset_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        eval_mod.load_dataset(path)


def test_load_manual_overrides_missing_file_returns_empty(tmp_path: Path) -> None:
    assert eval_mod.load_manual_overrides(tmp_path / "absent.jsonl") == {}


def test_load_manual_overrides_parses(tmp_path: Path) -> None:
    path = tmp_path / "manual.jsonl"
    _write_jsonl(path, [
        {"case_id": "c1", "mapped_outcome": "home_win", "confidence": 0.95},
        {"case_id": "c2", "mapped_outcome": "away_win", "confidence": 0.8},
    ])
    overrides = eval_mod.load_manual_overrides(path)
    assert set(overrides) == {"c1", "c2"}
    assert overrides["c1"]["mapped_outcome"] == "home_win"


def test_evaluate_match_rule_tp() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "home_win", "confidence_min": 0.9, "matcher_accept": ["rule"]},
    )
    rule_result = _MockMatchResult(confidence=0.95, mapped_outcome="home_win", reasoning="2/2")
    res = eval_mod._evaluate_match(
        "rule", case,
        rule_result=rule_result, llm_result=None, manual_overrides={},
    )
    assert res.predicted_accept is True
    assert res.true_accept is True
    assert res.outcome_ok is True
    assert res.confidence_ok is True


def test_evaluate_match_rule_fp_when_not_in_accept() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "home_win", "confidence_min": 0.9, "matcher_accept": []},
    )
    rule_result = _MockMatchResult(confidence=0.95, mapped_outcome="home_win", reasoning="2/2")
    res = eval_mod._evaluate_match(
        "rule", case,
        rule_result=rule_result, llm_result=None, manual_overrides={},
    )
    # predicted_accept True, true_accept False → FP
    assert res.predicted_accept is True
    assert res.true_accept is False


def test_evaluate_match_rule_fn_when_confidence_below_min() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "home_win", "confidence_min": 0.9, "matcher_accept": ["rule"]},
    )
    rule_result = _MockMatchResult(confidence=0.75, mapped_outcome="home_win", reasoning="1/2")
    res = eval_mod._evaluate_match(
        "rule", case,
        rule_result=rule_result, llm_result=None, manual_overrides={},
    )
    # predicted_accept False (confidence too low), true_accept True → FN
    assert res.predicted_accept is False
    assert res.true_accept is True
    assert res.outcome_ok is True
    assert res.confidence_ok is False


def test_evaluate_match_llm_none_when_expected_is_tn() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "none", "confidence_min": 0.0, "matcher_accept": []},
    )
    res = eval_mod._evaluate_match(
        "llm", case,
        rule_result=None, llm_result=None, manual_overrides={},
    )
    assert res.predicted_accept is False
    assert res.true_accept is False


def test_evaluate_match_manual_hit() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "away_win", "confidence_min": 0.5, "matcher_accept": ["manual"]},
    )
    overrides = {"c1": {"mapped_outcome": "away_win", "confidence": 0.8, "reasoning": "human"}}
    res = eval_mod._evaluate_match(
        "manual", case,
        rule_result=None, llm_result=None, manual_overrides=overrides,
    )
    assert res.predicted_accept is True
    assert res.true_accept is True
    assert res.detail == "human"


def test_evaluate_match_manual_missing_override_is_fn_when_expected() -> None:
    case = eval_mod.EvalCase(
        case_id="c1",
        match_id="m",
        market={},
        expected={"mapped_outcome": "away_win", "confidence_min": 0.5, "matcher_accept": ["manual"]},
    )
    res = eval_mod._evaluate_match(
        "manual", case,
        rule_result=None, llm_result=None, manual_overrides={},
    )
    assert res.predicted_accept is False
    assert res.true_accept is True


def test_aggregate_counts_tp_fp_fn_tn() -> None:
    results = [
        eval_mod.CaseResult(case_id="a", matcher="rule", predicted_accept=True, true_accept=True,
                            outcome_ok=True, confidence_ok=True, predicted_outcome="home_win",
                            predicted_confidence=0.95),
        eval_mod.CaseResult(case_id="b", matcher="rule", predicted_accept=True, true_accept=False,
                            outcome_ok=True, confidence_ok=True, predicted_outcome="home_win",
                            predicted_confidence=0.95),
        eval_mod.CaseResult(case_id="c", matcher="rule", predicted_accept=False, true_accept=True,
                            outcome_ok=False, confidence_ok=False, predicted_outcome=None,
                            predicted_confidence=None),
        eval_mod.CaseResult(case_id="d", matcher="rule", predicted_accept=False, true_accept=False,
                            outcome_ok=False, confidence_ok=False, predicted_outcome=None,
                            predicted_confidence=None),
    ]
    s = eval_mod._aggregate(results)
    assert (s.tp, s.fp, s.fn, s.tn) == (1, 1, 1, 1)
    assert s.precision == 0.5
    assert s.recall == 0.5
    assert s.f1 == 0.5


def test_run_eval_with_monkeypatched_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end run_eval with rule/llm/manual matchers monkeypatched."""
    dataset_path = tmp_path / "ds.jsonl"
    _write_jsonl(dataset_path, [
        _make_case(case_id="rule_hit", matcher_accept=["rule"], confidence_min=0.9),
        _make_case(case_id="llm_hit", matcher_accept=["llm"], confidence_min=0.85,
                   market_question="Will Messi lift the trophy?"),
        _make_case(case_id="reject", matcher_accept=[], expected_outcome="none",
                   confidence_min=0.0, market_question="Will it snow?"),
        _make_case(case_id="manual_only", matcher_accept=["manual"],
                   expected_outcome="away_win", confidence_min=0.5,
                   market_question="Will visitors cover?"),
    ])

    manual_path = tmp_path / "manual.jsonl"
    _write_jsonl(manual_path, [
        {"case_id": "manual_only", "mapped_outcome": "away_win", "confidence": 0.7,
         "reasoning": "human judged"},
    ])

    class _FakeBridge:
        def _rule_match(self, *, match_id, market_question, detected_teams, detected_competition):
            # rule hit only for rule_hit case
            if "rule_hit" in market_question or market_question == "Will Argentina win?":
                # The default case_id 'rule_hit' reuses the default market_question
                # 'Will Argentina win?' → return high-confidence match.
                return _MockMatchResult(0.95, "home_win", "2/2")
            return None

        async def _llm_match(self, *, match_id, market_question, detected_competition, detected_teams):
            if "Messi" in market_question:
                return _MockMatchResult(0.9, "home_win", "messi→arg")
            return None

    monkeypatch.setattr(eval_mod, "_build_bridge", lambda: _FakeBridge())

    cases = eval_mod.load_dataset(dataset_path)
    overrides = eval_mod.load_manual_overrides(manual_path)
    report_text, summaries = eval_mod.run_eval(
        cases, ["rule", "llm", "manual"],
        manual_overrides=overrides, report_format="text",
    )
    # rule: 1 TP (rule_hit), 0 FP, 1 FN (llm_hit expected rule? no → reject/manual_only not rule expected → TN)
    # Let's compute: rule expected_accept = {rule_hit}; predicted_accept = {rule_hit}
    # → rule TP=1, FP=0, FN=0, TN=3
    assert summaries["rule"].tp == 1
    assert summaries["rule"].fp == 0
    assert summaries["rule"].fn == 0
    assert summaries["rule"].tn == 3
    # llm expected_accept = {llm_hit}; predicted_accept = {llm_hit}
    assert summaries["llm"].tp == 1
    assert summaries["llm"].fn == 0
    # manual expected_accept = {manual_only}; predicted_accept = {manual_only}
    assert summaries["manual"].tp == 1
    assert summaries["manual"].fn == 0
    assert "=== Sport Market Link Matching Eval ===" in report_text


def test_run_eval_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_path = tmp_path / "ds.jsonl"
    _write_jsonl(dataset_path, [_make_case(case_id="r1", matcher_accept=["rule"])])

    class _FakeBridge:
        def _rule_match(self, **kwargs):
            return _MockMatchResult(0.95, "home_win", "2/2")

        async def _llm_match(self, **kwargs):
            return None

    monkeypatch.setattr(eval_mod, "_build_bridge", lambda: _FakeBridge())
    cases = eval_mod.load_dataset(dataset_path)
    report, summaries = eval_mod.run_eval(
        cases, ["rule"], manual_overrides={}, report_format="json",
    )
    assert isinstance(report, dict)
    assert "matchers" in report
    assert "rule" in report["matchers"]
    assert report["matchers"]["rule"]["summary"]["tp"] == 1


def test_main_cli_writes_report_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_path = tmp_path / "ds.jsonl"
    _write_jsonl(dataset_path, [_make_case(case_id="r1", matcher_accept=["rule"])])

    class _FakeBridge:
        def _rule_match(self, **kwargs):
            return _MockMatchResult(0.95, "home_win", "2/2")

        async def _llm_match(self, **kwargs):
            return None

    monkeypatch.setattr(eval_mod, "_build_bridge", lambda: _FakeBridge())
    out_path = tmp_path / "report.json"
    rc = eval_mod.main([
        "--dataset", str(dataset_path),
        "--matcher", "rule",
        "--report", "json",
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["matchers"]["rule"]["summary"]["tp"] == 1


def test_main_cli_returns_2_when_dataset_missing(tmp_path: Path) -> None:
    rc = eval_mod.main([
        "--dataset", str(tmp_path / "absent.jsonl"),
        "--matcher", "rule",
    ])
    assert rc == 2

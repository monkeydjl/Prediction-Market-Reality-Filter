import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchPredictionCard } from "./match-prediction-card";
import type {
  CalibrationBucket,
  CalibrationCandidate,
  ConfidenceCalibrationInfo,
  ExplanationEngineWeights,
  MatchFixtureRecord,
  MatchPredictionPayload,
} from "@/lib/generated-types";

// The fixtures below spell out every key of the published contracts (audit
// 53.76). A partial literal would stop being the shape the card actually
// receives, and a fixture that drifts from the contract is exactly what the
// generated types are here to prevent.
const FULL_PAYLOAD: MatchPredictionPayload = {
  predicted_score: { home: 2, away: 1 },
  outcome_probabilities: { home_win: 0.5, draw: 0.25, away_win: 0.25 },
  confidence: 0.72,
  prediction_method: null,
  engine_used: "hybrid",
  ai_reasoning: null,
  key_factors: null,
  last_updated: null,
  has_betting_odds: false,
  data_quality: "partial",
  data_quality_notes: [],
  data_quality_score: null,
  raw_confidence: null,
  confidence_calibration: null,
  high_confidence_selection: null,
  explanation_contributions: null,
};

const FULL_CALIBRATION: ConfidenceCalibrationInfo = {
  raw: 0.8,
  calibrated: 0.65,
  method: "piecewise_linear_reliability",
  engine_filter: null,
  total_samples: 8,
  min_total_samples: 6,
  min_bucket_samples: 3,
  is_reliable: true,
  bucket_is_reliable: true,
  is_reference_only: false,
  bucket: null,
  applied_bucket: null,
  reason: "piecewise_linear_calibration",
};

const FULL_CANDIDATE: CalibrationCandidate = {
  raw: 0.5,
  calibrated: 0.5,
  is_reliable: true,
  is_reference_only: false,
  total_samples: 8,
  min_total_samples: 6,
  min_bucket_samples: 3,
  bucket_is_reliable: true,
  bucket: null,
  applied_bucket: null,
  reason: "piecewise_linear_calibration",
};

const FULL_WEIGHTS: ExplanationEngineWeights = {
  elo_weight: 0.7,
  hybrid_weight: 0.3,
  source: "rule_default",
  samples: {},
  brier: {},
  learned_elo_weight: null,
  blend: null,
};

function bucket(label: string, count: number): CalibrationBucket {
  return { label, count, actual_accuracy: null, avg_confidence: null };
}

const match: MatchFixtureRecord = {
  match_id: "match-1",
  fixture_id: "1",
  home_team: "Argentina",
  away_team: "Brazil",
  kickoff_utc: "2026-06-24T12:00:00Z",
  venue: "MetLife Stadium",
  stage: "GROUP_STAGE",
  group: "A",
  status: "scheduled",
  home_score: null,
  away_score: null,
};

function prediction(
  overrides: Partial<MatchPredictionPayload>
): MatchPredictionPayload {
  return { ...FULL_PAYLOAD, ...overrides };
}

describe("MatchPredictionCard engine label", () => {
  it("shows kickoff UTC timestamps in Beijing time", () => {
    render(
      <MatchPredictionCard
        match={{ ...match, kickoff_utc: "2026-06-25T20:00:00" }}
        prediction={prediction({ engine_used: "hybrid" })}
      />
    );

    expect(screen.getByText("北京时间 6月26日 04:00")).toBeInTheDocument();
  });

  it("shows Elo+赔率 when the applied engine is elo_odds", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({ engine_used: "elo_odds" })}
      />
    );

    expect(screen.getByText("Elo+赔率")).toBeInTheDocument();
  });

  it("shows Elo+赔率 for Elo-only predictions that do have odds", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "elo_odds",
          prediction_method: "elo_only",
          // The producer always writes this key, and an odds-carrying
          // `elo_only` row is what keeps the public label here: without odds
          // the same method renders as "Elo only" (the next test).
          has_betting_odds: true,
        })}
      />
    );

    expect(screen.getByText("Elo+赔率")).toBeInTheDocument();
  });

  it("shows Elo-only and odds-unavailable status when no betting odds were used", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "elo_odds",
          prediction_method: "elo_only",
          has_betting_odds: false,
          data_quality: "partial",
          data_quality_notes: ["betting_odds_unavailable"],
        })}
      />
    );

    expect(screen.getByText("Elo only")).toBeInTheDocument();
    expect(screen.getByText("Odds unavailable")).toBeInTheDocument();
    expect(screen.getByText("Data quality")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });

  it("shows 混合引擎 when the applied engine is hybrid", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({ engine_used: "hybrid" })}
      />
    );

    expect(screen.getByText("混合引擎")).toBeInTheDocument();
  });

  it("shows 集成引擎 for integrated predictions", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "integrated",
          prediction_method: "integrated (elo_odds 40% + hybrid 60%)",
        })}
      />
    );

    expect(screen.getByText("集成引擎")).toBeInTheDocument();
  });

  it("shows confidence calibration details when available", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          confidence: 0.65,
          raw_confidence: 0.8,
          confidence_calibration: {
            ...FULL_CALIBRATION,
            raw: 0.8,
            calibrated: 0.65,
            total_samples: 8,
            is_reliable: true,
            bucket: bucket("80-100%", 4),
            applied_bucket: bucket("80-100%", 4),
            reason: "piecewise_linear_calibration",
          },
        })}
      />
    );

    expect(screen.getByText("置信校准")).toBeInTheDocument();
    expect(screen.getByText(/80%.*65%.*n=4/)).toBeInTheDocument();
  });

  it("shows low-sample calibration as reference-only", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          confidence: 0.72,
          raw_confidence: 0.72,
          confidence_calibration: {
            ...FULL_CALIBRATION,
            raw: 0.72,
            calibrated: 0.72,
            total_samples: 2,
            is_reliable: false,
            bucket_is_reliable: false,
            is_reference_only: true,
            bucket: bucket("60-80%", 2),
            applied_bucket: null,
            reason: "insufficient_total_samples",
          },
        })}
      />
    );

    expect(screen.getByText("置信校准")).toBeInTheDocument();
    expect(screen.getByText(/72%.*72%.*n=2\/6/)).toBeInTheDocument();
    expect(screen.getByText("校准样本不足，仅作参考")).toBeInTheDocument();
  });

  it("shows normalized historical non-real predictions as partial", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "hybrid",
          data_quality: "partial",
          data_quality_notes: ["historical_non_real_quality_normalized"],
        })}
      />
    );

    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText("历史非真实记录已降级")).toBeInTheDocument();
  });

  it("shows high-confidence engine selection details in the explanation panel", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "integrated",
          high_confidence_selection: {
            selected_engine: "integrated",
            selection_confidence: 0.88,
            candidate_confidences: {
              elo_odds: {
                ...FULL_CANDIDATE,
                raw: 0.3,
                calibrated: 0.3,
                is_reliable: false,
                is_reference_only: true,
                total_samples: 4,
                min_total_samples: 6,
              },
              hybrid: {
                ...FULL_CANDIDATE,
                raw: 0.95,
                calibrated: 0.5,
                total_samples: 8,
              },
              integrated: {
                ...FULL_CANDIDATE,
                raw: 0.84,
                calibrated: 0.88,
                total_samples: 8,
              },
            },
          },
        })}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /为何这样预测/ }));

    expect(screen.getByText("高置信选择")).toBeInTheDocument();
    expect(screen.getByText("Elo+赔率")).toBeInTheDocument();
    expect(screen.getByText("混合引擎")).toBeInTheDocument();
    expect(screen.getAllByText("集成引擎").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/30%.*n=4.*校准样本不足，仅作参考/)).toBeInTheDocument();
    expect(screen.getByText(/88%.*n=8/)).toBeInTheDocument();
  });

  it("shows explanation contribution breakdown in the explanation panel", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "integrated",
          explanation_contributions: {
            engine: "integrated",
            home_team: "Argentina",
            away_team: "Brazil",
            prediction_method: "integrated",
            engine_weights: {
              ...FULL_WEIGHTS,
              elo_weight: 0.7,
              hybrid_weight: 0.3,
            },
            items: [
              {
                key: "elo",
                label: "Elo",
                unit: "pp",
                home_impact: 5,
                away_impact: -3,
                description: "Elo差 +80 点，反映长期球队强度。",
                available: true,
              },
              {
                key: "odds",
                label: "赔率",
                unit: "pp",
                home_impact: 0,
                away_impact: 0,
                description: "没有可用真实赔率，赔率贡献未参与。",
                available: false,
              },
            ],
          },
        })}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /为何这样预测/ }));

    expect(screen.getByText("贡献拆解")).toBeInTheDocument();
    expect(screen.getByText("Elo 70% / 混合 30%")).toBeInTheDocument();
    expect(screen.getByText("Elo")).toBeInTheDocument();
    expect(screen.getByText(/阿根廷 \+5\.0pp/)).toBeInTheDocument();
    expect(screen.getByText(/巴西 -3\.0pp/)).toBeInTheDocument();
    expect(screen.getByText("赔率")).toBeInTheDocument();
    expect(screen.getByText("未参与")).toBeInTheDocument();
  });
});

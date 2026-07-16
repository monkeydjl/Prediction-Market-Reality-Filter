import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchPredictionCard } from "./match-prediction-card";
import type { MatchFixture, MatchPrediction } from "@/lib/world-cup/predictions-api";

const match: MatchFixture = {
  match_id: "match-1",
  fixture_id: 1,
  home_team: "Argentina",
  away_team: "Brazil",
  kickoff_utc: "2026-06-24T12:00:00Z",
  venue: "MetLife Stadium",
  stage: "GROUP_STAGE",
  group: "A",
  status: "scheduled",
};

function prediction(overrides: Partial<MatchPrediction>): MatchPrediction {
  return {
    predicted_score: { home: 2, away: 1 },
    outcome_probabilities: {
      home_win: 0.5,
      draw: 0.25,
      away_win: 0.25,
    },
    confidence: 0.72,
    ...overrides,
  };
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

  it("shows Elo+赔率 for Elo-only predictions", () => {
    render(
      <MatchPredictionCard
        match={match}
        prediction={prediction({
          engine_used: "elo_odds",
          prediction_method: "elo_only",
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
            raw: 0.8,
            calibrated: 0.65,
            method: "piecewise_linear_reliability",
            total_samples: 8,
            is_reliable: true,
            bucket: { label: "80-100%", count: 4 },
            applied_bucket: { label: "80-100%", count: 4 },
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
            raw: 0.72,
            calibrated: 0.72,
            method: "piecewise_linear_reliability",
            total_samples: 2,
            min_total_samples: 6,
            min_bucket_samples: 3,
            is_reliable: false,
            bucket_is_reliable: false,
            is_reference_only: true,
            bucket: { label: "60-80%", count: 2 },
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
                raw: 0.3,
                calibrated: 0.3,
                is_reliable: false,
                is_reference_only: true,
                total_samples: 4,
                min_total_samples: 6,
              },
              hybrid: { raw: 0.95, calibrated: 0.5, is_reliable: true, total_samples: 8 },
              integrated: { raw: 0.84, calibrated: 0.88, is_reliable: true, total_samples: 8 },
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
            engine_weights: { elo_weight: 0.7, hybrid_weight: 0.3, source: "rule_default" },
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

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MatchDetailPanel } from "./match-detail-panel";
import type { MatchDetail, PredictionResult } from "@/lib/sports-api";

function makeMatch(overrides: Partial<MatchDetail> = {}): MatchDetail {
  return {
    match_id: "m-1",
    sport: "football",
    competition: "epl",
    season_key: "2026",
    home_team: "Arsenal",
    away_team: "Chelsea",
    home_code: "ARS",
    away_code: "CHE",
    kickoff_utc: "2026-08-15T19:00:00Z",
    stage: "regular_season",
    round: null,
    ...overrides,
  };
}

function makePrediction(overrides: Partial<PredictionResult> = {}): PredictionResult {
  return {
    engine: "xgboost-v1",
    predicted_scores: { home: 1.8, away: 1.2 },
    outcome_probabilities: { home_win: 0.55, draw: 0.25, away_win: 0.2 },
    confidence: 0.72,
    explanation: [
      {
        factor: "elo",
        direction: "support",
        weight: 0.4,
        available: true,
        detail: "P(home_win)=0.55",
        predicted_outcome: "home_win",
      },
    ],
    feature_version: "v2.1",
    prediction_timestamp: "2026-07-17T10:00:00Z",
    ...overrides,
  };
}

describe("MatchDetailPanel", () => {
  it("渲染队名、运动图标和「预测」按钮", () => {
    const onPredict = vi.fn();
    render(
      <MatchDetailPanel
        match={makeMatch()}
        prediction={null}
        onPredict={onPredict}
        isPredicting={false}
      />,
    );
    expect(screen.getByText("Arsenal")).toBeInTheDocument();
    expect(screen.getByText("Chelsea")).toBeInTheDocument();
    // football 对应 ⚽ 图标
    expect(screen.getByText("⚽")).toBeInTheDocument();
    // 无预测时按钮文案为「预测」
    expect(screen.getByRole("button", { name: "预测" })).toBeInTheDocument();
  });

  it("点击预测按钮触发 onPredict 回调", () => {
    const onPredict = vi.fn();
    render(
      <MatchDetailPanel
        match={makeMatch()}
        prediction={null}
        onPredict={onPredict}
        isPredicting={false}
      />,
    );
    screen.getByRole("button", { name: "预测" }).click();
    expect(onPredict).toHaveBeenCalledTimes(1);
  });

  it("isPredicting 为 true 时按钮禁用且文案变为「预测中...」", () => {
    render(
      <MatchDetailPanel
        match={makeMatch()}
        prediction={null}
        onPredict={() => {}}
        isPredicting={true}
      />,
    );
    const btn = screen.getByRole("button", { name: "预测中..." });
    expect(btn).toBeDisabled();
  });

  it("已存在 prediction 时渲染胜率概率与因子分解区，按钮文案为「重新预测」", () => {
    render(
      <MatchDetailPanel
        match={makeMatch()}
        prediction={makePrediction()}
        onPredict={() => {}}
        isPredicting={false}
      />,
    );
    expect(screen.getByRole("button", { name: "重新预测" })).toBeInTheDocument();
    expect(screen.getByText("胜率概率")).toBeInTheDocument();
    expect(screen.getByText("因子分解")).toBeInTheDocument();
    // 预测比分（home: 1.8, away: 1.2）
    expect(screen.getByText(/home: 1\.8 \| away: 1\.2/)).toBeInTheDocument();
    // 置信度 0.72 → 72.0%
    expect(screen.getByText("72.0%")).toBeInTheDocument();
  });

  it("kickoff_utc 为 null 时显示「时间待定」", () => {
    render(
      <MatchDetailPanel
        match={makeMatch({ kickoff_utc: null })}
        prediction={null}
        onPredict={() => {}}
        isPredicting={false}
      />,
    );
    expect(screen.getByText("时间待定")).toBeInTheDocument();
  });
});

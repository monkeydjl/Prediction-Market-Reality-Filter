import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SportConfidencePanel } from "./sport-confidence-panel";
import type { ContributionItem, PredictionResult } from "@/lib/sports-api";

type PanelPrediction = Pick<
  PredictionResult,
  "confidence" | "outcome_probabilities" | "explanation" | "betting_analysis"
>;

function makeItem(overrides: Partial<ContributionItem> = {}): ContributionItem {
  return {
    factor: "elo",
    direction: "support",
    weight: 0.3,
    available: true,
    detail: "P(home_win)=0.65",
    predicted_outcome: "home_win",
    ...overrides,
  };
}

function makePrediction(overrides: Partial<PanelPrediction> = {}): PanelPrediction {
  return {
    confidence: 0.6,
    outcome_probabilities: { home_win: 0.5, away_win: 0.5 },
    explanation: [makeItem()],
    betting_analysis: null,
    ...overrides,
  };
}

/** The 决策强度 card is the first metric cell. */
function strengthText(): string {
  const panel = screen.getByTestId("sport-confidence-panel");
  const label = Array.from(panel.querySelectorAll("div")).find(
    (d) => d.textContent === "决策强度",
  );
  expect(label).toBeDefined();
  return label!.nextElementSibling!.textContent!.trim();
}

function agreementText(): string {
  const panel = screen.getByTestId("sport-confidence-panel");
  const label = Array.from(panel.querySelectorAll("div")).find(
    (d) => d.textContent === "因子一致性",
  );
  expect(label).toBeDefined();
  return label!.nextElementSibling!.textContent!.trim();
}

describe("SportConfidencePanel decision strength", () => {
  it("scores a flat binary distribution at 0%, not 33%", () => {
    // The defect: a hardcoded 1/3 baseline gave (0.5 - 1/3) / (2/3) = 25%,
    // and the older form gave 33%. Either way a coin flip read as signal.
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.5, away_win: 0.5 },
        })}
      />,
    );
    expect(strengthText()).toBe("0%");
  });

  it("scores a flat 3-way distribution at 0% as well", () => {
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 1 / 3, draw: 1 / 3, away_win: 1 / 3 },
        })}
      />,
    );
    expect(strengthText()).toBe("0%");
  });

  it("scores certainty at 100% for both arities", () => {
    const { unmount } = render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 1, away_win: 0 },
        })}
      />,
    );
    expect(strengthText()).toBe("100%");
    unmount();
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 1, draw: 0, away_win: 0 },
        })}
      />,
    );
    expect(strengthText()).toBe("100%");
  });

  it("separates the two arities at the same peak", () => {
    // peak 0.5: binary → 0%, 3-way → (0.5 - 1/3)/(2/3) = 25%. A single
    // hardcoded baseline cannot produce both numbers, so this pins the fix.
    const { unmount } = render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.5, away_win: 0.5 },
        })}
      />,
    );
    const binary = strengthText();
    unmount();
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.5, draw: 0.25, away_win: 0.25 },
        })}
      />,
    );
    expect(binary).toBe("0%");
    expect(strengthText()).toBe("25%");
  });

  it("prefers the API breakdown over the local mirror when present", () => {
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.5, away_win: 0.5 },
          betting_analysis: {
            confidence_breakdown: { decision_strength: 0.42 },
          } as unknown as PredictionResult["betting_analysis"],
        })}
      />,
    );
    expect(strengthText()).toBe("42%");
  });

  it("normalises an unnormalised distribution before scoring", () => {
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 5, away_win: 5 },
        })}
      />,
    );
    expect(strengthText()).toBe("0%");
  });
});

describe("SportConfidencePanel factor agreement", () => {
  it("excludes a level factor from both numerator and denominator", () => {
    // Two votes and one abstention: agreement is 1/2, not 1/3.
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.45, away_win: 0.55 },
          explanation: [
            makeItem({ factor: "elo", predicted_outcome: "home_win" }),
            makeItem({ factor: "rest", predicted_outcome: "away_win" }),
            makeItem({
              factor: "form",
              predicted_outcome: null,
              detail: "P(home_win)=0.5",
            }),
          ],
        })}
      />,
    );
    expect(agreementText()).toBe("50%");
  });

  it("counting the level factor as home would have read 33% instead", () => {
    // Same three factors, but the abstention replaced by the invented home
    // vote the backend used to publish. Recorded so the two are comparable.
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          outcome_probabilities: { home_win: 0.45, away_win: 0.55 },
          explanation: [
            makeItem({ factor: "elo", predicted_outcome: "home_win" }),
            makeItem({ factor: "rest", predicted_outcome: "away_win" }),
            makeItem({ factor: "form", predicted_outcome: "home_win" }),
          ],
        })}
      />,
    );
    expect(agreementText()).toBe("33%");
  });

  it("falls back to 50% when every factor abstains", () => {
    render(
      <SportConfidencePanel
        prediction={makePrediction({
          explanation: [
            makeItem({ factor: "elo", predicted_outcome: null }),
            makeItem({ factor: "rest", predicted_outcome: null }),
          ],
        })}
      />,
    );
    expect(agreementText()).toBe("50%");
  });
});

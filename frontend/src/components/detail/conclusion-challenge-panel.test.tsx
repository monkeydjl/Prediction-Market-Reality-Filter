import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConclusionChallengePanel } from "./conclusion-challenge-panel";

describe("ConclusionChallengePanel", () => {
  it("renders failed checks and retry metadata from the conclusion challenge gate", () => {
    render(
      <ConclusionChallengePanel
        challenge={{
          verdict: "revise",
          required_action: "recalculate_once",
          failed_checks: [
            {
              check: "confidence_calibration",
              severity: "error",
              reason: "概率校准不足",
            },
          ],
          warnings: [],
          challenge_summary: "否定门要求重新计算一次。",
          attempt_count: 1,
        }}
      />,
    );

    expect(screen.getByText("否定门")).toBeInTheDocument();
    expect(screen.getByText("需要重算")).toBeInTheDocument();
    expect(screen.getByText("否定门要求重新计算一次。")).toBeInTheDocument();
    expect(screen.getByText("confidence_calibration")).toBeInTheDocument();
    expect(screen.getByText("概率校准不足")).toBeInTheDocument();
    expect(screen.getByText("attempt 1")).toBeInTheDocument();
  });

  it("does not render when there is no challenge result", () => {
    const { container } = render(<ConclusionChallengePanel challenge={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

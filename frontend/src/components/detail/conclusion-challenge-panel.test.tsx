import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConclusionChallengePanel } from "./conclusion-challenge-panel";

describe("ConclusionChallengePanel", () => {
  it("renders nothing when the record carries no challenge result", () => {
    const { container } = render(<ConclusionChallengePanel challenge={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the verdict, required action and failed checks", () => {
    render(
      <ConclusionChallengePanel
        challenge={{
          verdict: "reject",
          required_action: "downgrade_to_wait",
          challenge_summary: "证据不足以支撑该结论。",
          failed_checks: [
            { check: "evidence_support", severity: "hard_fail", reason: "缺少官方来源。" },
          ],
          warnings: [{ check: "actionability", reason: "缺少时间边界。" }],
          attempt_count: 1,
        }}
      />,
    );

    const panel = screen.getByTestId("conclusion-challenge-panel");
    expect(panel).toHaveTextContent("已否定");
    expect(panel).toHaveTextContent("降级等待");
    expect(panel).toHaveTextContent("证据不足以支撑该结论。");
    expect(panel).toHaveTextContent("evidence_support");
    expect(panel).toHaveTextContent("缺少官方来源。");
    expect(panel).toHaveTextContent("actionability");
    expect(panel).toHaveTextContent("attempt 1");
  });

  it("falls back to raw values for unknown verdicts and actions", () => {
    render(
      <ConclusionChallengePanel
        challenge={{ verdict: "brand_new", required_action: "do_something" }}
      />,
    );

    const panel = screen.getByTestId("conclusion-challenge-panel");
    expect(panel).toHaveTextContent("brand_new");
    expect(panel).toHaveTextContent("do_something");
  });

  it("caps the rendered check list at six rows", () => {
    render(
      <ConclusionChallengePanel
        challenge={{
          verdict: "revise",
          required_action: "recalculate_once",
          failed_checks: Array.from({ length: 5 }, (_, i) => ({
            check: `fail_${i}`,
            reason: `原因 ${i}`,
          })),
          warnings: Array.from({ length: 5 }, (_, i) => ({
            check: `warn_${i}`,
            reason: `警告 ${i}`,
          })),
        }}
      />,
    );

    const panel = screen.getByTestId("conclusion-challenge-panel");
    expect(panel).toHaveTextContent("fail_4");
    expect(panel).toHaveTextContent("warn_0");
    // 5 failures + the first warning fill the cap; the rest are dropped.
    expect(panel).not.toHaveTextContent("warn_1");
  });
});

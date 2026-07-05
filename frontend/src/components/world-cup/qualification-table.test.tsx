import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualificationTable } from "./qualification-table";
import type { QualificationProbability } from "@/lib/qualification-probability";

function probability(
  team: string,
  currentPosition: number,
  qualificationProbability: number,
  qualificationStatus: QualificationProbability["qualificationStatus"],
): QualificationProbability {
  return {
    team,
    group: "A",
    currentPoints: 3,
    currentPosition,
    gamesPlayed: 3,
    gamesRemaining: 0,
    qualificationProbability,
    projectedPoints: 3,
    qualificationStatus,
  };
}

describe("QualificationTable", () => {
  it("shows qualification status badges for completed groups", () => {
    render(
      <QualificationTable
        probabilities={[
          probability("Brazil", 1, 1, "qualified"),
          probability("Switzerland", 2, 1, "qualified"),
          probability("Cameroon", 3, 0, "eliminated"),
          probability("Serbia", 4, 0, "eliminated"),
        ]}
      />
    );

    expect(screen.getAllByText("已出线")).toHaveLength(2);
    expect(screen.getAllByText("已淘汰")).toHaveLength(2);

    const brazilRow = screen.getByText("巴西").closest("div[class*='p-4']");
    expect(brazilRow).not.toBeNull();
    expect(within(brazilRow as HTMLElement).getByText("已出线")).toBeInTheDocument();
    expect(within(brazilRow as HTMLElement).getByText("100%")).toBeInTheDocument();

    const serbiaRow = screen.getByText("Serbia").closest("div[class*='p-4']");
    expect(serbiaRow).not.toBeNull();
    expect(within(serbiaRow as HTMLElement).getByText("已淘汰")).toBeInTheDocument();
    expect(within(serbiaRow as HTMLElement).getByText("0%")).toBeInTheDocument();
  });

  it("does not show final status badges for pending teams", () => {
    render(
      <QualificationTable
        probabilities={[
          probability("Brazil", 1, 0.82, "pending"),
          probability("Switzerland", 2, 0.61, "pending"),
        ]}
      />
    );

    expect(screen.queryByText("已出线")).not.toBeInTheDocument();
    expect(screen.queryByText("已淘汰")).not.toBeInTheDocument();
  });
});

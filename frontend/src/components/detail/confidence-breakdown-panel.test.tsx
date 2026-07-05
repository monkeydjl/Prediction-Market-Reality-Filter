import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceBreakdownPanel } from "./confidence-breakdown-panel";

const record = {
  event_id: "evt-1",
  event_title: "Will the agency approve the policy?",
  confidence_breakdown: {
    source_count: 4,
    independent_source_count: 4,
    official_source_count: 2,
    counterevidence_considered: true,
    news_quantity_score: 0.8,
    source_structure_score: 1,
    effective_source_score: 1,
    source_structure_used: true,
    source_quality_reasons: [
      "independent_source_support",
      "official_source_support",
      "counterevidence_considered",
    ],
  },
};

describe("ConfidenceBreakdownPanel", () => {
  it("shows source structure diagnostics when the backend provides them", () => {
    render(<ConfidenceBreakdownPanel record={record} />);

    expect(screen.getByText("Confidence source diagnostics")).toBeInTheDocument();
    expect(screen.getByText("4 total")).toBeInTheDocument();
    expect(screen.getByText("4 independent")).toBeInTheDocument();
    expect(screen.getByText("2 official")).toBeInTheDocument();
    expect(screen.getByText("Source structure lifted confidence")).toBeInTheDocument();
    expect(screen.getByText("independent source support")).toBeInTheDocument();
    expect(screen.getByText("official source support")).toBeInTheDocument();
    expect(screen.getByText("counterevidence considered")).toBeInTheDocument();
  });

  it("renders nothing when diagnostics are absent", () => {
    const { container } = render(<ConfidenceBreakdownPanel record={{}} />);

    expect(container).toBeEmptyDOMElement();
  });
});

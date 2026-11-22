import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DomainReliabilityPanel } from "./domain-reliability-panel";
import { qualityMetricsApi } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  qualityMetricsApi: {
    domainReliability: vi.fn(),
  },
}));

const domainMock = vi.mocked(qualityMetricsApi.domainReliability);

describe("DomainReliabilityPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    domainMock.mockResolvedValue({
      total_domains: 2,
      total_rows: 2,
      domains: [
        {
          domain: "reuters.com",
          category: "news",
          sample_count: 20,
          correct_count: 17,
          wrong_count: 3,
          credibility_sum: 18.2,
          reliability_score: 0.85,
          credibility_avg: 0.91,
          insufficient_samples: false,
          first_seen: "2026-07-01T00:00:00Z",
          last_updated: "2026-07-17T00:00:00Z",
        },
        {
          domain: "blog.example",
          category: "blog",
          sample_count: 3,
          correct_count: 1,
          wrong_count: 2,
          credibility_sum: 1.2,
          reliability_score: 0.33,
          credibility_avg: 0.4,
          insufficient_samples: true,
          first_seen: "2026-07-10T00:00:00Z",
          last_updated: "2026-07-16T00:00:00Z",
        },
      ],
    });
  });

  it("renders reliability rows with percentages", async () => {
    render(<DomainReliabilityPanel />);

    // The <section> renders during loading, so await the loaded content itself
    // rather than the test id — otherwise the assertions race the deferred load.
    await screen.findByText("reuters.com");
    const panel = screen.getByTestId("domain-reliability-panel");
    expect(panel).toHaveTextContent("来源域名可靠性");
    expect(panel).toHaveTextContent("85.0%");
    expect(panel).toHaveTextContent("正常");
  });

  it("flags a low-reliability domain over the insufficient-sample hint", async () => {
    render(<DomainReliabilityPanel />);

    await screen.findByText("blog.example");
    expect(screen.getByText("低可信")).toBeInTheDocument();
    expect(screen.queryByText("样本不足")).not.toBeInTheDocument();
  });

  it("renders the empty state when no domains are settled", async () => {
    // min_samples is a request parameter, not a response field: the route
    // returns domains/total_domains/total_rows/... and never echoes it.
    domainMock.mockResolvedValue({ total_domains: 0, total_rows: 0, domains: [] });
    render(<DomainReliabilityPanel />);

    expect(await screen.findByText(/尚无已结算事件/)).toBeInTheDocument();
  });
});

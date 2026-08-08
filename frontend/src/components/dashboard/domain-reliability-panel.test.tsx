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
      min_samples: 5,
      domains: [
        {
          domain: "reuters.com",
          category: "news",
          sample_count: 20,
          correct_count: 17,
          reliability_score: 0.85,
          credibility_avg: 0.91,
          insufficient_samples: false,
        },
        {
          domain: "blog.example",
          category: "blog",
          sample_count: 3,
          correct_count: 1,
          reliability_score: 0.33,
          credibility_avg: 0.4,
          insufficient_samples: true,
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
    domainMock.mockResolvedValue({ total_domains: 0, min_samples: 5, domains: [] });
    render(<DomainReliabilityPanel />);

    expect(await screen.findByText(/尚无已结算事件/)).toBeInTheDocument();
  });
});

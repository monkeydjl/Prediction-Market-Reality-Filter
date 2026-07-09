import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SystemStatus } from "./system-status";

const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  overview: vi.fn(),
  llmDiagnostics: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  eventsApi: apiMocks,
}));

describe("SystemStatus", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.health.mockResolvedValue({
      status: "ok",
      version: "test",
      loop: {
        scheduler: { running: true },
        runs: {},
        recent_runs: [],
        counts: {
          events: 3,
          resolved_events: 1,
          pending_links: 0,
          dangling_predictions: 0,
          dangling_links: 0,
          calibration_n: 2,
        },
      },
    });
    apiMocks.overview.mockResolvedValue({
      version: "test",
      endpoints: { events: {}, llm: {} },
    });
    apiMocks.llmDiagnostics.mockResolvedValue({
      configured_task_count: 1,
      unconfigured_task_count: 1,
      tasks: [
        {
          task: "probability_analysis",
          setting: "LLM_ROUTE_PROBABILITY_ANALYSIS",
          route_source: "task",
          configured: true,
          routes: [
            {
              provider: "deepseek",
              models: ["deepseek-reasoner", "deepseek-chat"],
              provider_configured: true,
              api_key_configured: true,
              base_url_configured: true,
            },
          ],
        },
        {
          task: "translation",
          setting: "LLM_ROUTE_TRANSLATION",
          route_source: "none",
          configured: false,
          routes: [],
        },
      ],
    });
  });

  it("shows redacted LLM gateway diagnostics in the expanded status panel", async () => {
    const user = userEvent.setup();
    render(<SystemStatus />);

    expect(await screen.findByText("LLM 路由 1/2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开系统状态" }));

    await waitFor(() => expect(apiMocks.llmDiagnostics).toHaveBeenCalled());
    expect(screen.getByText("LLM 网关路由")).toBeInTheDocument();
    expect(screen.getByText("概率分析")).toBeInTheDocument();
    expect(screen.getByText("LLM_ROUTE_PROBABILITY_ANALYSIS · 专用路由")).toBeInTheDocument();
    expect(screen.getByText("deepseek · 2 models · key yes")).toBeInTheDocument();
    expect(screen.getByText("标题翻译")).toBeInTheDocument();
    expect(screen.getByText("未解析到 provider/model 路由。")).toBeInTheDocument();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
  });
});

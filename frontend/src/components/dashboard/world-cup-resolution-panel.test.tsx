import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { eventsApi } from "@/lib/api";
import { WorldCupResolutionPanel } from "./world-cup-resolution-panel";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    worldCupResolveDryRun: vi.fn(),
    detail: vi.fn(),
    resolveManual: vi.fn(),
  },
}));

const api = eventsApi as unknown as {
  worldCupResolveDryRun: Mock;
  detail: Mock;
  resolveManual: Mock;
};

const dryRunResult = {
  status: "ok",
  dry_run: true,
  checked_count: 5,
  resolved_count: 1,
  pending_count: 4,
  unresolved_events: 5,
  matches: [
    {
      event_id: "wc-1",
      event_title: "Will Mexico reach the knockout stage?",
      actual_outcome: 100,
      confidence: 0.94,
      reason: "Mexico reached knockout_stage.",
      facts: ["wc2026:qualification:mexico:qualified"],
      result: "would_resolve",
    },
  ],
};

const detail = {
  event_id: "wc-1",
  record: {
    event_id: "wc-1",
    event_title: "Will Mexico reach the knockout stage?",
    source: {
      type: "sports_event",
      platform: "World Cup 2026",
      source_id: "world-cup-2026:mexico-knockout",
      url: "https://example.com/world-cup/mexico",
    },
  },
};

describe("WorldCupResolutionPanel", () => {
  beforeEach(() => {
    api.worldCupResolveDryRun.mockReset();
    api.detail.mockReset();
    api.resolveManual.mockReset();
    api.worldCupResolveDryRun.mockResolvedValue(dryRunResult);
    api.detail.mockResolvedValue(detail);
    api.resolveManual.mockResolvedValue({ event_id: "wc-1", record: detail.record });
  });

  it("loads World Cup dry-run candidates and enriches them with event source details", async () => {
    render(<WorldCupResolutionPanel />);

    expect(await screen.findByText("世界杯结算检查")).toBeInTheDocument();
    await waitFor(() => expect(api.worldCupResolveDryRun).toHaveBeenCalledWith(200));
    await waitFor(() => expect(api.detail).toHaveBeenCalledWith("wc-1"));

    expect(screen.getByText("1 candidates")).toBeInTheDocument();
    expect(screen.getByText("would resolve")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("confidence 0.94")).toBeInTheDocument();
    expect(screen.getByText("Mexico reached knockout_stage.")).toBeInTheDocument();
    expect(screen.getByText("wc2026:qualification:mexico:qualified")).toBeInTheDocument();
    expect(screen.getByText(/World Cup 2026 · world-cup-2026:mexico-knockout/)).toBeInTheDocument();
  });

  it("renders pending/no-match state when dry-run has no candidates", async () => {
    api.worldCupResolveDryRun.mockResolvedValue({
      status: "ok",
      dry_run: true,
      checked_count: 3,
      resolved_count: 0,
      pending_count: 3,
      unresolved_events: 3,
      matches: [],
    });

    render(<WorldCupResolutionPanel />);

    expect(await screen.findByText("no candidates")).toBeInTheDocument();
    expect(screen.getByText("当前没有可结算候选；待定事件仍需要更多事实或最终结果。")).toBeInTheDocument();
    expect(api.detail).not.toHaveBeenCalled();
  });

  it("refreshes the dry-run without writing settlements", async () => {
    render(<WorldCupResolutionPanel />);

    await screen.findByText("1 candidates");
    await userEvent.click(screen.getByRole("button", { name: "刷新检查" }));

    await waitFor(() => expect(api.worldCupResolveDryRun).toHaveBeenCalledTimes(2));
    expect(api.resolveManual).not.toHaveBeenCalled();
  });

  it("requires per-candidate confirmation before writing a World Cup resolution", async () => {
    render(<WorldCupResolutionPanel />);

    await screen.findByText("1 candidates");
    await userEvent.click(screen.getByRole("button", { name: "确认结算" }));

    expect(api.resolveManual).not.toHaveBeenCalled();
    expect(screen.getByText("再次确认后写入：结果 100%，置信度 0.94。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "写入结算" }));

    await waitFor(() => expect(api.resolveManual).toHaveBeenCalledWith("wc-1", {
      actual_outcome: 100,
      confidence: 0.94,
      notes: "Mexico reached knockout_stage.",
    }));
    await waitFor(() => expect(api.worldCupResolveDryRun).toHaveBeenCalledTimes(2));
    expect(screen.getByText("已写入结算：Will Mexico reach the knockout stage? 100%")).toBeInTheDocument();
  });

  it("keeps the candidate visible when a confirmed write fails", async () => {
    api.resolveManual.mockRejectedValue(new Error("operator key missing"));

    render(<WorldCupResolutionPanel />);

    await screen.findByText("1 candidates");
    await userEvent.click(screen.getByRole("button", { name: "确认结算" }));
    await userEvent.click(screen.getByRole("button", { name: "写入结算" }));

    expect(await screen.findByText("operator key missing")).toBeInTheDocument();
    expect(screen.getByText("Will Mexico reach the knockout stage?")).toBeInTheDocument();
  });

  it("renders an error state without reporting an empty candidate result", async () => {
    api.worldCupResolveDryRun.mockRejectedValue(new Error("operator key missing"));

    render(<WorldCupResolutionPanel />);

    expect(await screen.findByText("operator key missing")).toBeInTheDocument();
    expect(screen.getByText("dry-run 未返回结果。")).toBeInTheDocument();
    expect(screen.queryByText("当前没有可结算候选；待定事件仍需要更多事实或最终结果。")).not.toBeInTheDocument();
  });
});

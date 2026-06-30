import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { eventsApi } from "@/lib/api";
import { TrackingDecision } from "./tracking-decision";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    setTracking: vi.fn(),
  },
}));

describe("TrackingDecision", () => {
  beforeEach(() => {
    vi.mocked(eventsApi.setTracking).mockReset();
  });

  it("exposes selected status and priority as pressed buttons", async () => {
    vi.mocked(eventsApi.setTracking).mockResolvedValue({});
    render(<TrackingDecision id="evt-1" status="watching" priority="medium" />);

    expect(screen.getByRole("button", { name: "观察中" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "持续跟踪" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "中" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "持续跟踪" }));
    expect(eventsApi.setTracking).toHaveBeenCalledWith("evt-1", { status: "tracking" });
    expect(screen.getByRole("button", { name: "持续跟踪" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "观察中" })).toHaveAttribute("aria-pressed", "false");

    await waitFor(() => expect(screen.getByText("已同步")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "高" }));
    expect(eventsApi.setTracking).toHaveBeenCalledWith("evt-1", { priority: "high" });
    expect(screen.getByRole("button", { name: "高" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "中" })).toHaveAttribute("aria-pressed", "false");
  });

  it("restores the previous pressed state when saving fails", async () => {
    vi.mocked(eventsApi.setTracking).mockRejectedValue(new Error("offline"));
    render(<TrackingDecision id="evt-1" status="watching" priority="medium" />);

    await userEvent.click(screen.getByRole("button", { name: "已归档" }));

    await waitFor(() => expect(screen.getByText("保存失败")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "观察中" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "已归档" })).toHaveAttribute("aria-pressed", "false");
  });
});

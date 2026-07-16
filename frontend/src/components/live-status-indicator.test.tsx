import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { LiveStatusIndicator } from "./live-status-indicator";
import type { ApiHealth } from "@/lib/api";

// Mock SWR to control the data/error state returned to the component.
vi.mock("swr", () => ({
  default: vi.fn(),
}));

// Mock the API module so no real network call happens.
vi.mock("@/lib/api", () => ({
  eventsApi: {
    health: vi.fn(() => Promise.resolve({})),
  },
}));

import useSWR from "swr";

function mockSwrState(data: ApiHealth | undefined, error: unknown = undefined) {
  vi.mocked(useSWR).mockReturnValue({
    data,
    error,
    isLoading: !data && !error,
    isValidating: false,
    mutate: vi.fn(),
  } as ReturnType<typeof useSWR>);
}

describe("LiveStatusIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows connecting state when no data and no error", () => {
    mockSwrState(undefined, undefined);
    render(<LiveStatusIndicator />);
    expect(screen.getByLabelText("连接中")).toBeInTheDocument();
  });

  it("shows online state with green pulse when status is ok", () => {
    mockSwrState(
      { status: "ok", version: "1.0", loop: {} } as ApiHealth,
      undefined,
    );
    render(<LiveStatusIndicator />);
    const el = screen.getByLabelText("实时情报通道");
    expect(el).toBeInTheDocument();
    const dot = el.querySelector("span[aria-hidden='true']");
    expect(dot).toHaveClass("bg-pos");
    expect(dot).toHaveClass("animate-pulse");
  });

  it("shows degraded state with amber pulse when status is degraded", () => {
    mockSwrState(
      { status: "degraded", version: "1.0", loop: {} } as ApiHealth,
      undefined,
    );
    render(<LiveStatusIndicator />);
    const el = screen.getByLabelText("服务降级");
    expect(el).toBeInTheDocument();
    const dot = el.querySelector("span[aria-hidden='true']");
    expect(dot).toHaveClass("bg-warn");
    expect(dot).toHaveClass("animate-pulse");
  });

  it("shows offline state with red dot when fetch fails", () => {
    mockSwrState(undefined, new Error("Network error"));
    render(<LiveStatusIndicator />);
    const el = screen.getByLabelText("离线");
    expect(el).toBeInTheDocument();
    const dot = el.querySelector("span[aria-hidden='true']");
    expect(dot).toHaveClass("bg-neg");
    expect(dot).not.toHaveClass("animate-pulse");
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewQueueWorkbench } from "./review-queue-workbench";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  takeAction: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getOperatorId: vi.fn(() => "alice"),
    reviewQueueApi: api,
  };
});

describe("ReviewQueueWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.list.mockResolvedValue({
      status: "pending",
      count: 1,
      items: [
        {
          item_id: "rq-1",
          event_id: "evt-1",
          trigger: "audit_inconsistency",
          severity: "ERROR",
          reason: "字段一致性审计发现冲突",
          context: { conflict_type: "outcome_mismatch" },
          status: "pending",
          created_at: "2026-07-04T00:00:00Z",
        },
      ],
    });
    api.takeAction.mockResolvedValue({
      item: {
        item_id: "rq-1",
        event_id: "evt-1",
        trigger: "audit_inconsistency",
        severity: "ERROR",
        reason: "字段一致性审计发现冲突",
        context: {},
        status: "resolved",
      },
    });
  });

  it("renders pending review items with event link and context", async () => {
    render(<ReviewQueueWorkbench />);

    await waitFor(() => expect(screen.getByText("字段一致性审计发现冲突")).toBeInTheDocument());
    expect(screen.getByText("audit_inconsistency")).toBeInTheDocument();
    expect(screen.getByText("ERROR")).toBeInTheDocument();
    expect(screen.getByText(/outcome_mismatch/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /evt-1/ })).toHaveAttribute("href", "/events?id=evt-1");
  });

  it("submits reviewer action and refreshes the pending list", async () => {
    const user = userEvent.setup();
    render(<ReviewQueueWorkbench />);

    await screen.findByText("字段一致性审计发现冲突");
    await user.click(screen.getByRole("button", { name: "确认结论" }));

    await waitFor(() => {
      expect(api.takeAction).toHaveBeenCalledWith("rq-1", {
        reviewer: "alice",
        action: "confirm",
        note: "",
      });
    });
    expect(api.list).toHaveBeenCalledTimes(2);
  });
});

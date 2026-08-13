import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewQueueBoard } from "./review-queue-board";
import { reviewQueueApi, getOperatorId } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  reviewQueueApi: {
    list: vi.fn(),
    audit: vi.fn(),
    takeAction: vi.fn(),
  },
  getOperatorId: vi.fn(() => "alice"),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const listMock = vi.mocked(reviewQueueApi.list);
const auditMock = vi.mocked(reviewQueueApi.audit);
const actionMock = vi.mocked(reviewQueueApi.takeAction);

const REASON = "高价值事件被降级为 WAIT";

const PENDING_ITEM = {
  item_id: "item-1",
  event_id: "evt-001",
  trigger: "high_value_downgraded",
  severity: "WARN",
  reason: REASON,
  context: { final_direction: "WAIT" },
  status: "pending" as const,
  reviewer: null,
  reviewer_decision: null,
  reviewer_note: "",
  created_at: "2026-08-13 01:00:00",
  resolved_at: null,
};

describe("ReviewQueueBoard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getOperatorId).mockReturnValue("alice");
    listMock.mockResolvedValue({
      items: [PENDING_ITEM],
      count: 1,
      status: "pending",
    });
    auditMock.mockResolvedValue({ audit: [], count: 0, item_id: "item-1" });
    actionMock.mockResolvedValue({
      item: { ...PENDING_ITEM, status: "resolved", reviewer: "alice" },
    });
  });

  it("renders pending items with their trigger label and event link", async () => {
    render(<ReviewQueueBoard />);

    await screen.findByText(REASON);
    const board = screen.getByTestId("review-queue-board");
    expect(board).toHaveTextContent("高价值被降级");
    expect(screen.getByRole("link", { name: "evt-001" })).toHaveAttribute(
      "href",
      "/events?id=evt-001",
    );
  });

  it("requests the resolved list when the status filter changes", async () => {
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    await userEvent.selectOptions(
      screen.getByLabelText("队列状态"),
      "resolved",
    );

    await waitFor(() =>
      expect(listMock).toHaveBeenLastCalledWith({
        status: "resolved",
        trigger: undefined,
      }),
    );
  });

  it("passes the trigger filter to the API", async () => {
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    await userEvent.selectOptions(
      screen.getByLabelText("触发类型"),
      "source_market_conflict",
    );

    await waitFor(() =>
      expect(listMock).toHaveBeenLastCalledWith({
        status: "pending",
        trigger: "source_market_conflict",
      }),
    );
  });

  it("submits a reviewer action and reloads the list", async () => {
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    await userEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(auditMock).toHaveBeenCalledWith("item-1");

    // The operator id prefills the reviewer field, so no typing is required.
    await userEvent.selectOptions(screen.getByLabelText("处理动作"), "override");
    await userEvent.click(screen.getByRole("button", { name: /提交复核/ }));

    await waitFor(() =>
      expect(actionMock).toHaveBeenCalledWith("item-1", {
        reviewer: "alice",
        action: "override",
        note: "",
      }),
    );
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("surfaces an action failure without clearing the item", async () => {
    actionMock.mockRejectedValue(new Error("需要写入密钥"));
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    await userEvent.click(screen.getByRole("button", { name: "详情" }));
    await userEvent.click(screen.getByRole("button", { name: /提交复核/ }));

    expect(await screen.findByText("需要写入密钥")).toBeInTheDocument();
    expect(screen.getByText(REASON)).toBeInTheDocument();
  });

  it("shows the empty state and the load error state", async () => {
    listMock.mockResolvedValue({ items: [], count: 0, status: "pending" });
    const { unmount } = render(<ReviewQueueBoard />);
    expect(await screen.findByText(/当前没有待复核条目/)).toBeInTheDocument();
    unmount();

    listMock.mockRejectedValue(new Error("boom"));
    render(<ReviewQueueBoard />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});

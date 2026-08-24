import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewQueueBoard } from "./review-queue-board";
import { reviewQueueApi, getOperatorId } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  reviewQueueApi: {
    list: vi.fn(),
    sla: vi.fn(),
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
const slaMock = vi.mocked(reviewQueueApi.sla);
const auditMock = vi.mocked(reviewQueueApi.audit);
const actionMock = vi.mocked(reviewQueueApi.takeAction);

const REASON = "高价值事件被降级为 WAIT";

const PENDING_ITEM = {
  item_id: "item-1",
  event_id: "evt-001",
  trigger: "high_value_downgraded",
  severity: "WARN",
  severity_rank: 1,
  reason: REASON,
  context: { final_direction: "WAIT" },
  status: "pending" as const,
  reviewer: null,
  reviewer_decision: null,
  reviewer_note: "",
  created_at: "2026-08-13 01:00:00",
  age_hours: 50.0,
  resolved_at: null,
};

const SLA = {
  pending_total: 3,
  oldest_age_hours: 50.0,
  oldest_item_id: "item-1",
  breached_total: 0,
  unknown_severity: 0,
  sla_hours: { WARN: 72, ERROR: 24 },
  by_severity: {
    WARN: { count: 3, oldest_age_hours: 50.0, breached: 0, sla_hours: 72 },
  },
  by_trigger: { high_value_downgraded: 3 },
};

describe("ReviewQueueBoard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getOperatorId).mockReturnValue("alice");
    listMock.mockResolvedValue({
      items: [PENDING_ITEM],
      count: 1,
      total: 1,
      truncated: false,
      status: "pending",
    });
    slaMock.mockResolvedValue({ sla: SLA });
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
    listMock.mockResolvedValue({
      items: [],
      count: 0,
      total: 0,
      truncated: false,
      status: "pending",
    });
    const { unmount } = render(<ReviewQueueBoard />);
    expect(await screen.findByText(/当前没有待复核条目/)).toBeInTheDocument();
    unmount();

    listMock.mockRejectedValue(new Error("boom"));
    render(<ReviewQueueBoard />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  // ── Review SLA (Q7) ───────────────────────────────────────────────────
  // The queue had no age anywhere: the board printed the raw created_at
  // timestamp, so "how long has this waited" was left to the reviewer to work
  // out, and nothing on the page said the queue was breaching at all.

  it("shows how long each pending item has waited", async () => {
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    // The timestamp survives as the tooltip; the visible text is the wait.
    const chip = screen.getByTitle(PENDING_ITEM.created_at);
    expect(chip).toHaveTextContent("等待 2.1 天");
    expect(chip.className).not.toContain("text-neg");
  });

  it("highlights an item that is past its SLA budget", async () => {
    listMock.mockResolvedValue({
      items: [{ ...PENDING_ITEM, age_hours: 80.0 }],
      count: 1,
      total: 1,
      truncated: false,
      status: "pending",
    });
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    const chip = screen.getByTitle(PENDING_ITEM.created_at);
    expect(chip).toHaveTextContent("超时");
    expect(chip.className).toContain("text-neg");
  });

  it("does not call a severity with no budget a breach", async () => {
    // Mirrors the store: no budget means it can never breach, however old.
    listMock.mockResolvedValue({
      items: [{ ...PENDING_ITEM, severity: "CRITICAL", age_hours: 999.0 }],
      count: 1,
      total: 1,
      truncated: false,
      status: "pending",
    });
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    const chip = screen.getByTitle(PENDING_ITEM.created_at);
    expect(chip).not.toHaveTextContent("超时");
    expect(chip.className).not.toContain("text-neg");
  });

  it("falls back to the timestamp when an item carries no age", async () => {
    listMock.mockResolvedValue({
      items: [
        {
          ...PENDING_ITEM,
          age_hours: undefined,
          status: "resolved" as const,
          resolved_at: "2026-08-14 02:00:00",
        },
      ],
      count: 1,
      total: 1,
      truncated: false,
      status: "resolved",
    });
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    expect(screen.getByText(PENDING_ITEM.created_at)).toBeInTheDocument();
    expect(screen.queryByTitle(PENDING_ITEM.created_at)).toBeNull();
  });

  it("formats the wait in minutes, hours or days", async () => {
    const cases = [
      [0.5, "等待 30 分钟"],
      [5.25, "等待 5.3 小时"],
      [50.0, "等待 2.1 天"],
    ] as const;
    for (const [hours, text] of cases) {
      listMock.mockResolvedValue({
        items: [{ ...PENDING_ITEM, age_hours: hours }],
        count: 1,
        total: 1,
        truncated: false,
        status: "pending",
      });
      const { unmount } = render(<ReviewQueueBoard />);
      await screen.findByText(REASON);
      expect(screen.getByTitle(PENDING_ITEM.created_at)).toHaveTextContent(text);
      unmount();
    }
  });

  it("does not call an item exactly at its budget a breach", async () => {
    // Same rule as the store: strictly past the budget, not at it.
    listMock.mockResolvedValue({
      items: [{ ...PENDING_ITEM, age_hours: 72.0 }],
      count: 1,
      total: 1,
      truncated: false,
      status: "pending",
    });
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    const chip = screen.getByTitle(PENDING_ITEM.created_at);
    expect(chip).not.toHaveTextContent("超时");
    expect(chip.className).not.toContain("text-neg");
  });

  it("reports the queue depth, the oldest wait and the breach count", async () => {
    slaMock.mockResolvedValue({
      sla: { ...SLA, pending_total: 7, oldest_age_hours: 90.0, breached_total: 2 },
    });
    render(<ReviewQueueBoard />);

    const line = await screen.findByTestId("review-queue-sla");
    expect(line).toHaveTextContent("待复核 7");
    expect(line).toHaveTextContent("最久等待 3.8 天");
    expect(line).toHaveTextContent("超时 2");
    // Tightest budget first, independent of the object's key order.
    expect(line).toHaveTextContent("ERROR 24h · WARN 72h");
    expect(screen.getByTestId("review-queue-breached").className).toContain(
      "text-neg",
    );
  });

  it("does not raise the breach alarm when nothing has breached", async () => {
    render(<ReviewQueueBoard />);

    await screen.findByTestId("review-queue-sla");
    const breached = screen.getByTestId("review-queue-breached");
    expect(breached).toHaveTextContent("超时 0");
    expect(breached.className).not.toContain("text-neg");
  });

  it("reports pending items whose severity has no budget", async () => {
    slaMock.mockResolvedValue({ sla: { ...SLA, unknown_severity: 4 } });
    render(<ReviewQueueBoard />);

    expect(await screen.findByTestId("review-queue-sla")).toHaveTextContent(
      "无额度 4",
    );
  });

  it("keeps serving the list when the SLA summary fails", async () => {
    slaMock.mockRejectedValue(new Error("sla down"));
    render(<ReviewQueueBoard />);

    await screen.findByText(REASON);
    expect(screen.queryByTestId("review-queue-sla")).toBeNull();
    expect(screen.queryByText("sla down")).toBeNull();
    // No age chip either — without budgets a breach cannot be judged, but the
    // item's own age still comes from the list.
    expect(screen.getByTitle(PENDING_ITEM.created_at)).toHaveTextContent(
      "等待 2.1 天",
    );
  });

  it("says the list was truncated and which end was kept", async () => {
    listMock.mockResolvedValue({
      items: [PENDING_ITEM],
      count: 1,
      total: 130,
      truncated: true,
      status: "pending",
    });
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    expect(
      screen.getByText(/仅显示等待最久的 1 \/ 130 条/),
    ).toBeInTheDocument();
  });

  it("says nothing about truncation when the whole queue is shown", async () => {
    render(<ReviewQueueBoard />);
    await screen.findByText(REASON);

    expect(screen.queryByText(/仅显示等待最久的/)).toBeNull();
  });
});

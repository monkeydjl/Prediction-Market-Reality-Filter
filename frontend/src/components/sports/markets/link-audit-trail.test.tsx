import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MarketPriceAudit } from "@/lib/sports-api/types";

const apiMocks = vi.hoisted(() => ({
  useLinkAudit: vi.fn(),
}));
// Partial mock: the component also reads the `auditZero` / `auditFlagChips`
// classifiers and `AUDIT_ZERO_TEXT` from the same barrel, so spreading the real
// module keeps them while replacing only the hook.
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useLinkAudit: apiMocks.useLinkAudit,
}));

import { LinkAuditTrail } from "./link-audit-trail";

function mockAudit(overrides: Partial<MarketPriceAudit> = {}) {
  apiMocks.useLinkAudit.mockReturnValue({
    data: {
      link_id: 7,
      available: true,
      snapshot_count: 12,
      first_price: 0.42,
      last_price: 0.51,
      delta_pp: 9.0,
      max_drawdown_pp: 2.1,
      min_price: 0.4,
      max_price: 0.53,
      first_captured_at: "2026-07-01T08:00:00Z",
      last_captured_at: "2026-07-05T20:00:00Z",
      flags: [],
      ...overrides,
    },
    error: null,
    isLoading: false,
  });
}

// Locks the direction-color convention 涨红跌绿 (CN market convention) on the
// audit-trail Δ readout: the price-path net change rising = red (--up),
// falling = green (--down). Valence --pos/--neg must not appear here.
describe("LinkAuditTrail Δ direction colors", () => {
  beforeEach(() => {
    apiMocks.useLinkAudit.mockReset();
  });

  it("colors a rising price path red (up token, not pos)", () => {
    mockAudit({ delta_pp: 9.0 });
    render(<LinkAuditTrail linkId={7} />);
    const delta = screen.getByText("+9.0pp");
    expect(delta).toHaveClass("text-up");
    expect(delta).not.toHaveClass("text-pos");
    expect(delta).not.toHaveClass("text-neg");
  });

  it("colors a falling price path green (down token, not neg)", () => {
    mockAudit({ delta_pp: -3.4 });
    render(<LinkAuditTrail linkId={7} />);
    const delta = screen.getByText("-3.4pp");
    expect(delta).toHaveClass("text-down");
    expect(delta).not.toHaveClass("text-neg");
    expect(delta).not.toHaveClass("text-pos");
  });

  it("leaves a missing delta neutral", () => {
    mockAudit({ delta_pp: undefined });
    render(<LinkAuditTrail linkId={7} />);
    const delta = screen.getByText("—");
    expect(delta).not.toHaveClass("text-up");
    expect(delta).not.toHaveClass("text-down");
    expect(delta).not.toHaveClass("text-pos");
    expect(delta).not.toHaveClass("text-neg");
  });
});

// The three zeroes (audit 53.180). `available: false` used to print
// 「尚无可用价格序列」 whether the store had answered or not; the backend now
// says which, and this door has to stop merging them back together.
describe("LinkAuditTrail zero states", () => {
  beforeEach(() => {
    apiMocks.useLinkAudit.mockReset();
  });

  it("names a failed read instead of calling it an empty series", () => {
    mockAudit({
      available: false,
      read_failed: true,
      snapshot_count: 0,
      flags: ["read_failed"],
    });
    render(<LinkAuditTrail linkId={7} />);
    const line = screen.getByTestId("link-audit-read-failed-7");
    expect(line.textContent).toContain("这不是「尚无快照」");
    expect(screen.queryByText(/尚无可用价格序列/)).toBeNull();
    // A server fault is bad news, so the valence token, not a direction token.
    expect(line).toHaveClass("text-neg");
    expect(line).not.toHaveClass("text-up");
    expect(line).not.toHaveClass("text-down");
  });

  it("does not additionally render the raw flag as a pill", () => {
    mockAudit({ available: false, read_failed: true, flags: ["read_failed"] });
    render(<LinkAuditTrail linkId={7} />);
    expect(screen.queryByText("read_failed")).toBeNull();
  });

  it("keeps the old wording when the read really did succeed", () => {
    mockAudit({
      available: false,
      read_failed: false,
      snapshot_count: 0,
      flags: ["no_snapshots"],
    });
    render(<LinkAuditTrail linkId={7} />);
    expect(screen.getByText(/尚无可用价格序列/)).toBeInTheDocument();
    expect(screen.getByText("无快照")).toBeInTheDocument();
    expect(screen.queryByTestId("link-audit-read-failed-7")).toBeNull();
  });

  it("reports an unmarked body as unknown, not as empty", () => {
    mockAudit({ available: false, snapshot_count: 3, flags: [] });
    render(<LinkAuditTrail linkId={7} />);
    expect(screen.getByText(/价格序列状态未知/)).toBeInTheDocument();
    expect(screen.queryByText(/尚无可用价格序列/)).toBeNull();
  });

  it("still draws the numbers when the path is available", () => {
    // Guard the guard: the new branch must not swallow the success one.
    mockAudit({ read_failed: false });
    render(<LinkAuditTrail linkId={7} />);
    expect(screen.getByText("+9.0pp")).toBeInTheDocument();
    expect(screen.queryByTestId("link-audit-read-failed-7")).toBeNull();
  });
});

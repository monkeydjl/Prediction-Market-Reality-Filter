import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MarketPriceAudit } from "@/lib/sports-api/types";

const apiMocks = vi.hoisted(() => ({
  useLinkAudit: vi.fn(),
}));
vi.mock("@/lib/sports-api", () => ({
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

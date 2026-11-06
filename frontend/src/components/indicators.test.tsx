import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeltaPill } from "./indicators";

// Locks the direction-color convention 涨红跌绿 (CN market convention):
// rising = red (--up tokens), falling = green (--down tokens). The valence
// tokens --pos (ok=green) / --neg (fail=red) must NOT appear on direction
// pills -- they belong to status/success/error UI. See globals.css for the
// token split and the components' doc comments.
describe("DeltaPill direction colors", () => {
  it("renders rising deltas red (up tokens, not pos)", () => {
    render(<DeltaPill delta={3.4} />);
    const pill = screen.getByText("+3.4pt").closest("span");
    expect(pill).not.toBeNull();
    expect(pill).toHaveClass("text-up");
    expect(pill).toHaveClass("bg-up/15");
    expect(pill).not.toHaveClass("text-pos");
    expect(pill).not.toHaveClass("bg-pos/15");
    expect(pill.querySelector(".lucide-arrow-up-right")).not.toBeNull();
  });

  it("renders falling deltas green (down tokens, not neg)", () => {
    render(<DeltaPill delta={-3.4} />);
    const pill = screen.getByText("-3.4pt").closest("span");
    expect(pill).not.toBeNull();
    expect(pill).toHaveClass("text-down");
    expect(pill).toHaveClass("bg-down/15");
    expect(pill).not.toHaveClass("text-neg");
    expect(pill).not.toHaveClass("bg-neg/15");
    expect(pill.querySelector(".lucide-arrow-down-right")).not.toBeNull();
  });

  it("renders flat deltas neutral (no direction tokens)", () => {
    render(<DeltaPill delta={0.3} />);
    const pill = screen.getByText("+0.3pt").closest("span");
    expect(pill).not.toBeNull();
    expect(pill).toHaveClass("text-muted-foreground");
    expect(pill).not.toHaveClass("text-up");
    expect(pill).not.toHaveClass("text-down");
    expect(pill).not.toHaveClass("text-pos");
    expect(pill).not.toHaveClass("text-neg");
  });
});

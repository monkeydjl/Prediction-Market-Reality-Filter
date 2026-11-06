import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WeightDiffTable } from "./WeightDiffTable";
import type { WeightDiffRow } from "@/lib/sports-api/param-weights";

// Locks the direction-color convention 涨红跌绿 (CN market convention) for
// weight deltas: increase = red (--up), decrease = green (--down). Valence
// tokens --pos/--neg must not appear on delta cells. See globals.css.
const rows: WeightDiffRow[] = [
  { factor: "elo_home", before: 0.3, after: 0.35, delta: 0.05 },
  { factor: "form", before: 0.2, after: 0.17, delta: -0.03 },
  { factor: "rest", before: 0.1, after: 0.1, delta: 0 },
  { factor: "h2h", before: null, after: 0.08, delta: null },
];

describe("WeightDiffTable direction colors", () => {
  it("colors positive deltas red and negative deltas green (up/down tokens)", () => {
    render(<WeightDiffTable rows={rows} />);

    const upCell = screen.getByTestId("weight-row-elo_home").querySelectorAll("td")[3];
    expect(upCell).toHaveClass("text-up");
    expect(upCell).not.toHaveClass("text-pos");
    expect(upCell).not.toHaveClass("text-neg");

    const downCell = screen.getByTestId("weight-row-form").querySelectorAll("td")[3];
    expect(downCell).toHaveClass("text-down");
    expect(downCell).not.toHaveClass("text-neg");
    expect(downCell).not.toHaveClass("text-pos");

    const flatCell = screen.getByTestId("weight-row-rest").querySelectorAll("td")[3];
    expect(flatCell).toHaveClass("text-muted-foreground");

    const nullCell = screen.getByTestId("weight-row-h2h").querySelectorAll("td")[3];
    expect(nullCell).toHaveClass("text-muted-foreground");
    expect(nullCell).toHaveTextContent("—");
  });
});

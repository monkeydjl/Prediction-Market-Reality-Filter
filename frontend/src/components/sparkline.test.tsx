import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Sparkline } from "./sparkline";

// Locks the direction-color convention 涨红跌绿 (CN market convention):
// up-trend = red (--color-up), down-trend = green (--color-down). Valence
// tokens --pos/--neg must not appear on trend lines. See globals.css.
describe("Sparkline direction colors", () => {
  it("strokes up-trends with the up (red) token", () => {
    const { container } = render(
      <Sparkline data={[10, 20, 30]} trend="up" />,
    );
    const line = container.querySelector("polyline");
    expect(line).not.toBeNull();
    expect(line.getAttribute("stroke")).toBe("var(--color-up)");
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toContain("整体上行");
  });

  it("strokes down-trends with the down (green) token", () => {
    const { container } = render(
      <Sparkline data={[30, 20, 10]} trend="down" />,
    );
    const line = container.querySelector("polyline");
    expect(line).not.toBeNull();
    expect(line.getAttribute("stroke")).toBe("var(--color-down)");
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toContain("整体下行");
  });

  it("strokes flat trends with muted foreground (no direction tokens)", () => {
    const { container } = render(
      <Sparkline data={[20, 20.2, 19.9]} trend="flat" />,
    );
    const line = container.querySelector("polyline");
    expect(line).not.toBeNull();
    expect(line.getAttribute("stroke")).toBe("var(--color-muted-foreground)");
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toContain("整体平稳");
  });
});

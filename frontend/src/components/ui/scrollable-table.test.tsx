import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScrollableTable } from "./scrollable-table";

describe("ScrollableTable", () => {
  it("exposes region with aria-label and scroll class", () => {
    render(
      <ScrollableTable aria-label="测试表格" testId="scroll-table">
        <table>
          <tbody>
            <tr>
              <td>cell</td>
            </tr>
          </tbody>
        </table>
      </ScrollableTable>,
    );
    const region = screen.getByRole("region", { name: "测试表格" });
    expect(region).toHaveAttribute("data-testid", "scroll-table");
    expect(region.className).toMatch(/overflow-x-auto/);
    expect(screen.getByText("cell")).toBeInTheDocument();
  });
});

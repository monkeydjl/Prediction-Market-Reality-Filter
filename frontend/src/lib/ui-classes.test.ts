import { describe, expect, it } from "vitest";
import { inputCls, selectCls, tableScrollClassName } from "./ui-classes";

describe("ui-classes", () => {
  it("inputCls includes focus ring and border tokens", () => {
    const cls = inputCls();
    expect(cls).toMatch(/border-border/);
    expect(cls).toMatch(/focus-visible:ring-2/);
  });

  it("inputCls merges extra classes", () => {
    expect(inputCls("h-24 resize-y")).toMatch(/resize-y/);
  });

  it("selectCls sm size uses h-9", () => {
    expect(selectCls(undefined, "sm")).toMatch(/h-9/);
  });

  it("tableScrollClassName enables overflow-x", () => {
    expect(tableScrollClassName).toMatch(/overflow-x-auto/);
  });
});

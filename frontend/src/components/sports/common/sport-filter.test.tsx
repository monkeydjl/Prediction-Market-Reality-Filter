import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SportFilter } from "./sport-filter";

describe("SportFilter", () => {
  it("渲染全部运动选项", () => {
    render(<SportFilter value={null} onChange={() => {}} />);
    expect(screen.getByText("全部")).toBeInTheDocument();
    expect(screen.getByText("Football")).toBeInTheDocument();
    expect(screen.getByText("Basketball")).toBeInTheDocument();
    expect(screen.getByText("Baseball")).toBeInTheDocument();
    expect(screen.getByText("Hockey")).toBeInTheDocument();
  });

  it("点击选项时调用 onChange 并传入对应 code", () => {
    const onChange = vi.fn();
    render(<SportFilter value={null} onChange={onChange} />);
    screen.getByText("Basketball").click();
    expect(onChange).toHaveBeenCalledWith("basketball");
  });

  it("点击「全部」时传入 null", () => {
    const onChange = vi.fn();
    render(<SportFilter value="football" onChange={onChange} />);
    screen.getByText("全部").click();
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("当前选中项应用激活样式", () => {
    render(<SportFilter value="football" onChange={() => {}} />);
    const footballBtn = screen.getByText("Football");
    // 激活样式为 "bg-secondary text-foreground"（非 hover 变体）
    expect(footballBtn.className).toContain("bg-secondary text-foreground");
    // 未选中项不含激活样式 "bg-secondary text-foreground"（仅含 hover:bg-secondary/60）
    const basketballBtn = screen.getByText("Basketball");
    expect(basketballBtn.className).not.toContain("bg-secondary text-foreground");
    expect(basketballBtn.className).toContain("text-muted-foreground");
  });
});

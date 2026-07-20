import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SportTrackBanner } from "./sport-track-banner";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

describe("SportTrackBanner", () => {
  it("kernel track links to world-cup special", () => {
    render(<SportTrackBanner track="kernel" />);
    const banner = screen.getByTestId("sport-track-banner");
    expect(banner).toHaveTextContent("Kernel 多体育赛程");
    expect(banner).toHaveTextContent("/api/sports/*");
    const link = screen.getByRole("link", { name: /世界杯专题/ });
    expect(link).toHaveAttribute("href", "/sports/world-cup");
  });

  it("world_cup track links back to kernel list", () => {
    render(<SportTrackBanner track="world_cup" />);
    const banner = screen.getByTestId("sport-track-banner");
    expect(banner).toHaveTextContent("世界杯专题");
    expect(banner).toHaveTextContent("/api/world-cup/*");
    expect(screen.getByRole("link", { name: /体育预测/ })).toHaveAttribute(
      "href",
      "/sports",
    );
    expect(screen.getByRole("link", { name: /学习仪表盘/ })).toHaveAttribute(
      "href",
      "/sports/learning",
    );
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DomainScopeBanner } from "./domain-scope-banner";

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

describe("DomainScopeBanner", () => {
  it("event scope links to sports edges", () => {
    render(<DomainScopeBanner domain="event" />);
    const banner = screen.getByTestId("domain-scope-banner");
    expect(banner).toHaveTextContent("事件情报 Edge");
    expect(banner).toHaveTextContent("不是");
    const link = screen.getByRole("link", { name: /体育 · Edge 偏离/ });
    expect(link).toHaveAttribute("href", "/sports/edges");
  });

  it("sport scope links to event edges", () => {
    render(<DomainScopeBanner domain="sport" />);
    const banner = screen.getByTestId("domain-scope-banner");
    expect(banner).toHaveTextContent("体育 Edge 偏离");
    const link = screen.getByRole("link", { name: /事件 · Edge 监测/ });
    expect(link).toHaveAttribute("href", "/edges");
  });
});

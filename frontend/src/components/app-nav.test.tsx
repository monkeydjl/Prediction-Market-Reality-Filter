import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppNav } from "./app-nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/review-queue",
}));

describe("AppNav", () => {
  it("links to the review queue workbench", () => {
    render(<AppNav />);

    expect(screen.getByRole("link", { name: /复核队列/ })).toHaveAttribute(
      "href",
      "/review-queue",
    );
  });
});

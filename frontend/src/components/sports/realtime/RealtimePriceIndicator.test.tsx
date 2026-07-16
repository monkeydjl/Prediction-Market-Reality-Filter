import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RealtimePriceIndicator } from "./RealtimePriceIndicator";

describe("RealtimePriceIndicator", () => {
  it("shows LIVE when connected", () => {
    render(<RealtimePriceIndicator isConnected={true} />);
    expect(screen.getByText("LIVE")).toBeDefined();
  });

  it("shows OFFLINE when disconnected", () => {
    render(<RealtimePriceIndicator isConnected={false} />);
    expect(screen.getByText("OFFLINE")).toBeDefined();
  });

  it("renders nothing when matchId is null", () => {
    const { container } = render(
      <RealtimePriceIndicator isConnected={false} matchId={null} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

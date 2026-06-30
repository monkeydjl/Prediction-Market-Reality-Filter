import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EngineAutoTuneDashboard } from "./engine-auto-tune-dashboard";

describe("EngineAutoTuneDashboard", () => {
  it("offers the integrated engine for auto tuning", () => {
    render(<EngineAutoTuneDashboard />);

    expect(screen.getByText("集成引擎")).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProbabilityBar } from "./probability-bar";

describe("ProbabilityBar", () => {
  it("renders two outcomes for binary", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const bars = screen.getAllByRole("img", { name: /概率/ });
    expect(bars).toHaveLength(2);
  });

  it("renders three outcomes for ternary", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.5, draw: 0.25, away_win: 0.25 }}
        homeTeam="Brazil"
        awayTeam="Argentina"
      />,
    );
    const bars = screen.getAllByRole("img", { name: /概率/ });
    expect(bars).toHaveLength(3);
  });

  it("renders correct percentages", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    expect(screen.getByText("62.0%")).toBeDefined();
    expect(screen.getByText("38.0%")).toBeDefined();
  });

  it("applies home color to home_win", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const homeBar = screen.getByRole("img", { name: /Lakers.*概率/ });
    expect(homeBar).toHaveClass("bg-chart-1");
  });

  it("applies away color to away_win", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.62, away_win: 0.38 }}
        homeTeam="Lakers"
        awayTeam="Celtics"
      />,
    );
    const awayBar = screen.getByRole("img", { name: /Celtics.*概率/ });
    expect(awayBar).toHaveClass("bg-chart-4");
  });

  it("applies neutral color to draw", () => {
    render(
      <ProbabilityBar
        probabilities={{ home_win: 0.5, draw: 0.25, away_win: 0.25 }}
        homeTeam="Brazil"
        awayTeam="Argentina"
      />,
    );
    const drawBar = screen.getByRole("img", { name: /平局.*概率/ });
    expect(drawBar).toHaveClass("bg-chart-5");
  });
});

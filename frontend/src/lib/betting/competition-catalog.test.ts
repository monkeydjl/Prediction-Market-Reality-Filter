import { describe, expect, it } from "vitest";
import {
  BETTING_COMPETITIONS,
  BETTING_TOOL_LINKS,
  competitionsBySection,
  getCompetitionById,
  statusLabel,
} from "./competition-catalog";

describe("competition-catalog", () => {
  it("includes world cup, big-five football aliases, NBA, and esports placeholder", () => {
    const ids = BETTING_COMPETITIONS.map((c) => c.id);
    expect(ids).toEqual(
      expect.arrayContaining([
        "world-cup",
        "football",
        "epl",
        "laliga",
        "bundesliga",
        "serie-a",
        "ligue-1",
        "nba",
        "mlb",
        "nhl",
        "esports",
      ]),
    );
  });

  it("world-cup uses dedicated track and live status", () => {
    const wc = getCompetitionById("world-cup");
    expect(wc?.track).toBe("world_cup");
    expect(wc?.status).toBe("live");
    expect(wc?.href).toBe("/sports/world-cup");
  });

  it("esports is coming_soon placeholder without kernel sport", () => {
    const es = getCompetitionById("esports");
    expect(es?.status).toBe("coming_soon");
    expect(es?.track).toBe("placeholder");
    expect(es?.kernelSport).toBeUndefined();
  });

  it("kernel competitions expose kernelSport for list filtering", () => {
    expect(getCompetitionById("nba")?.kernelSport).toBe("basketball");
    expect(getCompetitionById("epl")?.kernelSport).toBe("football");
  });

  it("groups football section with world cup + leagues", () => {
    const football = competitionsBySection("football");
    expect(football.length).toBeGreaterThanOrEqual(6);
    expect(football.every((c) => c.section === "football")).toBe(true);
  });

  it("tool links cover edges and recommendations", () => {
    const hrefs = BETTING_TOOL_LINKS.map((t) => t.href);
    expect(hrefs).toContain("/sports/edges");
    expect(hrefs).toContain("/sports/recommendations");
  });

  it("statusLabel is Chinese for known statuses", () => {
    expect(statusLabel("live")).toMatch(/上线/);
    expect(statusLabel("coming_soon")).toMatch(/即将/);
  });
});

import { describe, expect, it } from "vitest";
import {
  BETTING_COMPETITIONS,
  BETTING_TOOL_LINKS,
  competitionsBySection,
  getCompetitionByCode,
  getCompetitionById,
  kernelCompetitionChips,
  mergeCompetitionsWithLive,
  normalizeCompetitionCode,
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
        "lol",
      ]),
    );
  });

  it("getCompetitionById returns world cup live track", () => {
    const wc = getCompetitionById("world-cup");
    expect(wc?.status).toBe("live");
    expect(wc?.track).toBe("world_cup");
    expect(wc?.href).toBe("/sports/world-cup");
  });

  it("esports is coming_soon without fake markets", () => {
    const es = getCompetitionById("esports");
    expect(es?.status).toBe("coming_soon");
    expect(es?.track).toBe("placeholder");
  });

  it("getCompetitionById returns lol coming_soon esports track", () => {
    const lol = getCompetitionById("lol");
    expect(lol?.status).toBe("coming_soon");
    expect(lol?.track).toBe("placeholder");
    expect(lol?.section).toBe("esports");
    expect(lol?.sport).toBe("lol");
    expect(lol?.kernelSport).toBe("lol");
    expect(lol?.competitionCode).toBe("lol");
    expect(lol?.href).toBe("/sports/betting/lol");
  });

  it("kernel competitions expose kernelSport and competitionCode", () => {
    expect(getCompetitionById("nba")?.kernelSport).toBe("basketball");
    expect(getCompetitionById("nba")?.competitionCode).toBe("nba");
    expect(getCompetitionById("epl")?.kernelSport).toBe("football");
    expect(getCompetitionById("epl")?.competitionCode).toBe("epl");
    expect(getCompetitionById("epl")?.href).toContain("competition=epl");
  });

  it("competitionsBySection groups football and tools", () => {
    const football = competitionsBySection("football");
    expect(football.length).toBeGreaterThan(0);
    expect(football.every((c) => c.section === "football")).toBe(true);
    expect(BETTING_TOOL_LINKS.length).toBeGreaterThan(0);
  });

  it("statusLabel is Chinese for known statuses", () => {
    expect(statusLabel("live")).toMatch(/上线/);
    expect(statusLabel("coming_soon")).toMatch(/即将/);
  });

  it("mergeCompetitionsWithLive overlays adapter_likely without inventing ids", () => {
    const merged = mergeCompetitionsWithLive(BETTING_COMPETITIONS, [
      { id: "epl", adapter_likely: true, label: "英超（live）" },
      { id: "ghost-league", adapter_likely: true },
    ]);
    const epl = merged.find((c) => c.id === "epl");
    expect(epl?.adapterLikely).toBe(true);
    expect(epl?.label).toBe("英超（live）");
    expect(merged.some((c) => c.id === "ghost-league")).toBe(false);
    expect(mergeCompetitionsWithLive(BETTING_COMPETITIONS, null)[0].id).toBe(
      BETTING_COMPETITIONS[0].id,
    );
  });

  it("normalizeCompetitionCode maps aliases", () => {
    expect(normalizeCompetitionCode("PL")).toBe("epl");
    expect(normalizeCompetitionCode("serie-a")).toBe("serie_a");
    expect(normalizeCompetitionCode("  ")).toBeNull();
  });

  it("getCompetitionByCode and kernelCompetitionChips", () => {
    expect(getCompetitionByCode("epl")?.id).toBe("epl");
    expect(getCompetitionByCode("nba")?.kernelSport).toBe("basketball");
    const football = kernelCompetitionChips("football");
    expect(football.every((c) => c.kernelSport === "football")).toBe(true);
    expect(football.some((c) => c.id === "epl")).toBe(true);
    expect(kernelCompetitionChips("basketball").some((c) => c.id === "nba")).toBe(
      true,
    );
  });
});

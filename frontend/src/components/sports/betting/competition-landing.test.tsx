import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompetitionLanding } from "./competition-landing";
import type { BettingCompetition } from "@/lib/betting/competition-catalog";
import { OPERATOR_CREDENTIALS_EVENT } from "@/lib/operator-credentials";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

const useBettingCatalog = vi.fn();
const useBettingStatus = vi.fn();
const useMatches = vi.fn();
const syncSchedule = vi.fn();
const hasOperatorApiKey = vi.fn();

vi.mock("@/lib/operator-credentials", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/operator-credentials")>();
  return {
    ...actual,
    hasOperatorApiKey: () => hasOperatorApiKey(),
  };
});

vi.mock("@/lib/sports-api", () => ({
  useBettingCatalog: () => useBettingCatalog(),
  useBettingStatus: () => useBettingStatus(),
  useMatches: (...args: unknown[]) => useMatches(...args),
  syncSchedule: (...args: unknown[]) => syncSchedule(...args),
}));

const epl: BettingCompetition = {
  id: "epl",
  sport: "football",
  label: "英超",
  shortLabel: "英超",
  description: "EPL",
  status: "kernel",
  href: "/sports?sport=football&competition=epl",
  kernelSport: "football",
  competitionCode: "epl",
  track: "kernel",
  section: "football",
};

const esports: BettingCompetition = {
  id: "esports",
  sport: "esports",
  label: "电竞",
  shortLabel: "电竞",
  description: "placeholder",
  status: "coming_soon",
  href: "/sports/betting/esports",
  track: "placeholder",
  section: "esports",
};

const lol: BettingCompetition = {
  id: "lol",
  sport: "lol",
  label: "英雄联盟",
  shortLabel: "LoL",
  description: "Kernel sport=lol",
  status: "coming_soon",
  href: "/sports/betting/lol",
  kernelSport: "lol",
  competitionCode: "lol",
  track: "placeholder",
  section: "esports",
};

describe("CompetitionLanding", () => {
  beforeEach(() => {
    hasOperatorApiKey.mockReturnValue(false);
    syncSchedule.mockReset();
    useBettingStatus.mockReturnValue({
      data: {
        version: 1,
        kernel_ready: true,
        registered_prefixes: ["epl-", "wc-"],
        kernel_error: null,
      },
    });
    useBettingCatalog.mockReturnValue({
      data: {
        version: 1,
        competitions: [{ id: "epl", adapter_likely: true }],
        tools: [],
        sections: {},
        flags: { kernel_prediction_enabled: true, epl_data_enabled: true },
      },
      error: undefined,
      isLoading: false,
    });
    useMatches.mockReturnValue({
      data: [
        {
          match_id: "epl-1",
          home_team: "Arsenal",
          away_team: "Chelsea",
          has_prediction: true,
        },
        {
          match_id: "epl-2",
          home_team: "Liverpool",
          away_team: "City",
          has_prediction: false,
        },
      ],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
  });

  it("shows adapter badge, match count, preview, and runtime prefix", () => {
    render(<CompetitionLanding competition={epl} />);
    expect(screen.getByTestId("landing-adapter-status")).toHaveAttribute(
      "data-adapter-likely",
      "true",
    );
    expect(screen.getByTestId("landing-match-count-n")).toHaveTextContent("2");
    expect(screen.getByTestId("landing-match-preview")).toHaveTextContent(
      "Arsenal",
    );
    expect(screen.getByTestId("landing-runtime-prefix")).toHaveTextContent(
      "epl-",
    );
    expect(useMatches).toHaveBeenCalledWith({
      sport: "football",
      competition: "epl",
    });
    expect(screen.queryByTestId("landing-sync-schedule")).not.toBeInTheDocument();
  });

  it("shows sync button and calls syncSchedule when operator key present", async () => {
    hasOperatorApiKey.mockReturnValue(true);
    syncSchedule.mockResolvedValue({
      synced: 3,
      sport: "football",
      competition: "epl",
      competition_normalized: "epl",
      registered_prefixes: ["epl-"],
    });
    const mutate = vi.fn();
    useMatches.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate,
    });
    render(<CompetitionLanding competition={epl} />);
    const btn = await screen.findByTestId("landing-sync-schedule");
    await userEvent.click(btn);
    expect(syncSchedule).toHaveBeenCalledWith({
      sport: "football",
      competition: "epl",
    });
    expect(await screen.findByTestId("landing-sync-msg")).toHaveTextContent(
      "3",
    );
    expect(mutate).toHaveBeenCalled();
  });

  it("does not poll matches for coming_soon esports", () => {
    useBettingCatalog.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
    });
    useMatches.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
    render(<CompetitionLanding competition={esports} />);
    expect(useMatches).toHaveBeenCalledWith(null);
    expect(screen.getByRole("status")).toHaveTextContent(/不会展示占位赔率/);
    expect(screen.queryByTestId("lol-dry-run-ops")).not.toBeInTheDocument();
  });

  it("shows LoL dry-run ops panel without polling matches or fake odds", () => {
    useBettingCatalog.mockReturnValue({
      data: {
        version: 1,
        competitions: [{ id: "lol", adapter_likely: true }],
        tools: [],
        sections: {},
        flags: {
          phase_lol_enabled: true,
          lol_dry_run_import: true,
          lol_dry_run_path_configured: true,
        },
      },
      error: undefined,
      isLoading: false,
    });
    useBettingStatus.mockReturnValue({
      data: {
        version: 1,
        kernel_ready: true,
        registered_prefixes: ["lol-"],
        kernel_error: null,
        lol: {
          schedule_vendor: "null",
          effective_schedule_vendor: "null",
          schedule_source_blocked: false,
          production_http_client_ready: false,
          settle_grace_hours: 6,
        },
      },
    });
    useMatches.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
    render(<CompetitionLanding competition={lol} />);
    expect(useMatches).toHaveBeenCalledWith(null);
    expect(screen.getByRole("status")).toHaveTextContent(/不会展示占位赔率/);
    const ops = screen.getByTestId("lol-dry-run-ops");
    expect(ops).toHaveTextContent(/PHASE_LOL_ENABLED=ON/);
    expect(ops).toHaveTextContent(/LOL_DRY_RUN_IMPORT=ON/);
    expect(ops).toHaveTextContent(/lol- 已注册/);
    expect(ops).toHaveTextContent(/LOL_SCHEDULE_VENDOR=null/);
    expect(ops).toHaveTextContent(/http_client_ready=no/);
    expect(ops).toHaveTextContent(/GATES/);
    expect(screen.queryByTestId("landing-sync-schedule")).not.toBeInTheDocument();
    expect(screen.queryByTestId("landing-match-count")).not.toBeInTheDocument();
  });
});

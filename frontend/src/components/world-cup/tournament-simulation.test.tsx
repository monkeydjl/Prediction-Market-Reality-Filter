import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { analyticsApi } from "@/lib/analytics-api";
import TournamentSimulation from "./tournament-simulation";

vi.mock("@/lib/analytics-api", () => ({
  analyticsApi: {
    tournamentSimulation: vi.fn(),
  },
}));

const baseResult = {
  win_probability: { Argentina: 0.42, France: 0.21 },
  reach_final: { Argentina: 0.6, France: 0.31 },
  reach_semifinal: { Argentina: 0.75, France: 0.55 },
  most_likely_winner: "Argentina",
  most_likely_winner_prob: 0.42,
  simulations: 1000,
  completed_simulations: 1000,
  elapsed_ms: 120,
  qualification_state: {
    eliminated_teams: [],
    qualified_teams: ["Argentina", "France"],
    eliminated_count: 0,
    qualified_count: 2,
    qualification_fact_count: 2,
  },
  real_data_readiness: {
    ok: true,
    qualification_source_configured: true,
    qualification_fact_count: 2,
    match_result_count: 0,
    scheduled_import_enabled: true,
    last_import_failed: false,
    issues: [],
    issue_details: [],
  },
  excluded_teams: [],
};

describe("TournamentSimulation", () => {
  beforeEach(() => {
    vi.mocked(analyticsApi.tournamentSimulation).mockReset();
  });

  it("filters eliminated teams out of probability result sections", async () => {
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue({
      win_probability: { Brazil: 0, Argentina: 0.42, France: 0.21 },
      reach_final: { Brazil: 0, Argentina: 0.6, France: 0.31 },
      reach_semifinal: { Brazil: 0, Argentina: 0.75, France: 0.55 },
      most_likely_winner: "Brazil",
      most_likely_winner_prob: 0.9,
      simulations: 1000,
      completed_simulations: 1000,
      elapsed_ms: 120,
      qualification_state: {
        eliminated_teams: ["Brazil"],
        qualified_teams: ["Argentina", "France"],
        eliminated_count: 1,
        qualified_count: 2,
        qualification_fact_count: 3,
      },
      excluded_teams: ["Brazil"],
    });

    render(<TournamentSimulation />);

    await waitFor(() => expect(screen.getByText("夺冠概率")).toBeInTheDocument());

    for (const title of ["夺冠概率", "进入决赛", "进入四强"]) {
      const section = screen.getByText(title).closest(".rounded-lg");
      expect(section).not.toBeNull();
      expect(within(section as HTMLElement).getByText("阿根廷")).toBeInTheDocument();
      expect(within(section as HTMLElement).queryByText("巴西")).not.toBeInTheDocument();
    }

    expect(screen.getByText(/淘汰状态已生效/)).toBeInTheDocument();
    expect(screen.getByText(/巴西/)).toBeInTheDocument();
  });

  it("shows the live contender count and knockout fixture basis", async () => {
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue({
      ...baseResult,
      simulation_basis: "knockout_fixtures",
      remaining_team_count: 9,
      locked_result_count: 7,
      simulated_match_count: 1,
      excluded_teams: ["Brazil", "Germany", "Japan", "Morocco", "USA", "Mexico", "Italy"],
    });

    render(<TournamentSimulation />);

    expect(await screen.findByText("仍在争冠路径")).toBeInTheDocument();
    expect(screen.getByText("9 支")).toBeInTheDocument();
    expect(screen.getByText("淘汰赛赛程")).toBeInTheDocument();
    expect(screen.getByText("已锁定 7 场 · 待模拟 1 场")).toBeInTheDocument();
  });

  it("shows the simulation evidence summary from real tournament state", async () => {
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue({
      ...baseResult,
      simulation_basis: "knockout_fixtures",
      remaining_team_count: 9,
      locked_result_count: 7,
      simulated_match_count: 1,
      locked_results: [
        {
          stage: "ROUND_OF_16",
          status: "finished",
          home_team: "Argentina",
          away_team: "France",
          home_score: 2,
          away_score: 1,
          winner: "Argentina",
          loser: "France",
        },
      ],
      simulated_fixtures: [
        {
          stage: "ROUND_OF_16",
          status: "scheduled",
          home_team: "England",
          away_team: "Spain",
        },
      ],
      qualification_state: {
        eliminated_teams: ["Brazil", "Germany"],
        qualified_teams: ["Argentina", "France"],
        eliminated_count: 2,
        qualified_count: 2,
        qualification_fact_count: 48,
        latest_observed_at: "2026-07-08T08:00:00Z",
      },
      real_data_readiness: {
        ...baseResult.real_data_readiness,
        qualification_fact_count: 48,
        match_result_count: 7,
      },
      excluded_teams: ["Brazil", "Germany"],
    });

    render(<TournamentSimulation />);

    expect(await screen.findByText("模拟数据依据")).toBeInTheDocument();
    expect(screen.getByText("真实淘汰/出线事实")).toBeInTheDocument();
    expect(screen.getByText("48 条")).toBeInTheDocument();
    expect(screen.getByText("锁定赛果")).toBeInTheDocument();
    expect(screen.getByText("7 场")).toBeInTheDocument();
    expect(screen.getByText("待模拟赛程")).toBeInTheDocument();
    expect(screen.getByText("1 场")).toBeInTheDocument();
    expect(screen.getByText(/已剔除 2 支/)).toBeInTheDocument();
    expect(screen.getByText("锁定赛果明细")).toBeInTheDocument();
    expect(screen.getByText(/阿根廷 2-1 法国/)).toBeInTheDocument();
    expect(screen.getByText("待模拟赛程明细")).toBeInTheDocument();
    expect(screen.getByText(/英格兰 vs 西班牙/)).toBeInTheDocument();
  });

  it("explains that simulation uses real tournament state plus Elo odds engine", async () => {
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue(baseResult);

    render(<TournamentSimulation />);

    expect(await screen.findByText(/真实出线\/赛果状态/)).toBeInTheDocument();
    expect(screen.getByText(/Elo\/赔率引擎模拟 1,000 次/)).toBeInTheDocument();
  });

  it("blocks trusted probability output when real data readiness is incomplete", async () => {
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue({
      ...baseResult,
      real_data_readiness: {
        ok: false,
        qualification_source_configured: false,
        qualification_fact_count: 0,
        match_result_count: 94,
        scheduled_import_enabled: false,
        last_import_failed: true,
        issues: ["qualification_facts_missing"],
        issue_details: [
          {
            code: "qualification_facts_missing",
            severity: "error",
            message: "尚未导入真实出线/淘汰事实",
            action: "从真实 standings 源导入 qualification facts 后再信任出线状态。",
          },
        ],
      },
    });

    render(<TournamentSimulation />);

    await waitFor(() => expect(screen.getByText("模拟数据完整性不足")).toBeInTheDocument());
    expect(screen.getByText("模拟数据完整性不足")).toBeInTheDocument();
    expect(screen.getByText("数据未就绪，不展示可信冠军概率。")).toBeInTheDocument();
    expect(screen.getByText("尚未导入真实出线/淘汰事实")).toBeInTheDocument();
    expect(screen.getByText("从真实 standings 源导入 qualification facts 后再信任出线状态。")).toBeInTheDocument();
    expect(screen.queryByText("最可能冠军")).not.toBeInTheDocument();
    expect(screen.queryByText("进入决赛")).not.toBeInTheDocument();
    expect(screen.queryByText("进入四强")).not.toBeInTheDocument();
  });

  it("offers the operator path to post-match backfill when a stale knockout fixture blocks trusted output", async () => {
    const openAnalytics = vi.fn();
    vi.mocked(analyticsApi.tournamentSimulation).mockResolvedValue({
      ...baseResult,
      real_data_readiness: {
        ok: false,
        issues: ["stale_unfinished_knockout_fixture"],
        issue_details: [
          {
            code: "stale_unfinished_knockout_fixture",
            severity: "error",
            match_id: "fd-537382",
            stage: "ROUND_OF_16",
            kickoff_utc: "2026-07-07T20:00:00+00:00",
            message: "Switzerland vs Colombia 已过开球时间，但状态仍为 scheduled。",
            action: "先刷新真实比赛数据源，或回填最终比分，再信任冠军概率。",
          },
        ],
      },
    });

    render(<TournamentSimulation onOpenAnalytics={openAnalytics} />);

    await waitFor(() => expect(screen.getByText("模拟数据完整性不足")).toBeInTheDocument());
    expect(screen.getByText(/Switzerland vs Colombia 已过开球时间/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "打开赛后回填面板" }));

    expect(openAnalytics).toHaveBeenCalledTimes(1);
  });


  it("requests a forced refresh when the user clicks simulate again", async () => {
    vi.mocked(analyticsApi.tournamentSimulation)
      .mockResolvedValueOnce(baseResult)
      .mockResolvedValueOnce({
        ...baseResult,
        cached: false,
        win_probability: { France: 0.5, Argentina: 0.3 },
        most_likely_winner: "France",
        most_likely_winner_prob: 0.5,
      });

    render(<TournamentSimulation />);

    await waitFor(() => expect(screen.getByText("夺冠概率")).toBeInTheDocument());
    expect(analyticsApi.tournamentSimulation).toHaveBeenNthCalledWith(1);

    await userEvent.click(screen.getByRole("button", { name: "再次模拟" }));

    await waitFor(() => expect(analyticsApi.tournamentSimulation).toHaveBeenCalledTimes(2));
    expect(analyticsApi.tournamentSimulation).toHaveBeenNthCalledWith(2, 1000, true);
    await waitFor(() => expect(screen.getAllByText("法国")[0]).toBeInTheDocument());
  });

  it("keeps simulate again available after an initial simulation error", async () => {
    vi.mocked(analyticsApi.tournamentSimulation)
      .mockResolvedValueOnce({
        error: "insufficient_group_data",
        message: "Need at least 2 groups of fixture data to run tournament simulation.",
      })
      .mockResolvedValueOnce({
        ...baseResult,
        cached: false,
      });

    render(<TournamentSimulation />);

    await waitFor(() => expect(screen.getByText(/Need at least 2 groups/)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "再次模拟" }));

    await waitFor(() => expect(analyticsApi.tournamentSimulation).toHaveBeenCalledTimes(2));
    expect(analyticsApi.tournamentSimulation).toHaveBeenNthCalledWith(2, 1000, true);
    await waitFor(() => expect(screen.getByText("夺冠概率")).toBeInTheDocument());
  });

});

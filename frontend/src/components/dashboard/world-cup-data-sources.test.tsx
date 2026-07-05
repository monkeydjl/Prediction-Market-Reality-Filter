import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { eventsApi } from "@/lib/api";
import { WorldCupDataSources } from "./world-cup-data-sources";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    worldCupDataSourcesStatus: vi.fn(),
    worldCupDataSourcePreview: vi.fn(),
    worldCupDataSourceImport: vi.fn(),
    worldCupResolveDryRun: vi.fn(),
  },
}));

const api = eventsApi as unknown as {
  worldCupDataSourcesStatus: Mock;
  worldCupDataSourcePreview: Mock;
  worldCupDataSourceImport: Mock;
  worldCupResolveDryRun: Mock;
};

const status = {
  facts: {
    count: 4,
    by_kind: { match_result: 2, qualification: 1, team_stat: 1 },
    last_updated: "2026-06-23T00:00:00Z",
  },
  configured_sources: {
    data_file: { configured: true, path: "world_cup_data.json", exists: false },
    bundle_file: { configured: true, path: "world_cup_source_bundle.json", exists: true },
    bundle_url: { configured: true, source_url: "https://example.com/bundle" },
    feeds: [
      { kind: "matches", configured: true, source_url: "https://example.com/matches" },
      { kind: "statistics", configured: false, source_url: "" },
    ],
    api_football: {
      configured: true,
      base_url: "https://api-football.example/v3",
      league_id: "1",
      season: "2026",
      fetch_events: true,
      fetch_lineups: false,
      fetch_statistics: true,
      max_detail_calls: 25,
    },
    sportmonks: {
      configured: true,
      feeds: [
        { kind: "matches", configured: true, source_url: "https://sportmonks.example/fixtures" },
        { kind: "standings", configured: false, source_url: "" },
      ],
    },
  },
  scheduled_import: {
    enabled: true,
    mode: "api_football",
    replace: false,
    hour_utc: 5,
    minute_utc: 20,
  },
  runs: {
    world_cup_source_bundle_import: {
      status: "success",
      started_at: "2026-06-23T05:20:00Z",
      finished_at: "2026-06-23T05:20:02Z",
      duration_ms: 2000,
      result: {
        mode: "api_football",
        converted_fact_count: 3,
        imported: 3,
        source_fetch_count: 2,
        source_fetches: [
          {
            kind: "matches",
            source_url: "https://api-football.example/v3/fixtures",
            status: "success",
            duration_ms: 12,
          },
        ],
        call_budget: {
          fixture_count: 1,
          max_detail_calls: 25,
          detail_calls_used: 0,
          detail_calls_skipped: 0,
          detail_calls_remaining: 25,
          enabled_detail_feeds: ["match_events", "statistics"],
        },
      },
    },
  },
};

describe("WorldCupDataSources", () => {
  beforeEach(() => {
    api.worldCupDataSourcesStatus.mockReset();
    api.worldCupDataSourcePreview.mockReset();
    api.worldCupDataSourceImport.mockReset();
    api.worldCupResolveDryRun.mockReset();
    api.worldCupDataSourcesStatus.mockResolvedValue(status);
    api.worldCupDataSourcePreview.mockResolvedValue({
      provider: "api_football",
      source_count: 2,
      converted_fact_count: 7,
      error_count: 1,
      source_url: "https://api-football.example/v3/fixtures",
      source_feeds: [
        { kind: "matches", source_url: "https://api-football.example/v3/fixtures" },
        { kind: "statistics", source_url: "https://api-football.example/v3/statistics" },
      ],
      source_fetch_count: 2,
      source_fetches: [
        {
          kind: "matches",
          source_url: "https://api-football.example/v3/fixtures",
          status: "success",
          duration_ms: 11,
        },
      ],
      call_budget: {
        fixture_count: 1,
        max_detail_calls: 25,
        detail_calls_used: 0,
        detail_calls_remaining: 25,
      },
      errors: [{ index: 2, error: "invalid fact" }],
    });
    api.worldCupDataSourceImport.mockResolvedValue({
      source_count: 1,
      converted_fact_count: 4,
      imported: 4,
      replace: true,
    });
    api.worldCupResolveDryRun.mockResolvedValue({
      status: "ok",
      dry_run: true,
      checked_count: 3,
      resolved_count: 1,
      pending_count: 2,
      unresolved_events: 3,
      matches: [
        {
          event_id: "wc-1",
          event_title: "Will Mexico reach the knockout stage?",
          actual_outcome: 100,
          confidence: 1,
          reason: "Mexico reached knockout_stage.",
          facts: ["wc2026:qualification:mexico:qualified"],
          result: "would_resolve",
        },
      ],
    });
  });

  it("renders configured source status and last run metrics", async () => {
    render(<WorldCupDataSources />);

    expect(await screen.findByText("facts 4")).toBeInTheDocument();
    expect(screen.getByText("API-Football")).toBeInTheDocument();
    expect(screen.getByText("budget 25")).toBeInTheDocument();
    expect(screen.getByText("https://api-football.example/v3")).toBeInTheDocument();
    expect(screen.getByText("https://example.com/matches")).toBeInTheDocument();
    expect(screen.getByText("https://sportmonks.example/fixtures")).toBeInTheDocument();
    expect(screen.getByText("match_result")).toBeInTheDocument();
    expect(screen.getByText("最近定时导入")).toBeInTheDocument();
    expect(screen.getByText("https://api-football.example/v3/fixtures")).toBeInTheDocument();
  });

  it("previews a configured provider source", async () => {
    render(<WorldCupDataSources />);

    await screen.findByText("API-Football");
    await userEvent.click(screen.getByRole("button", { name: "API-Football 预览" }));

    await waitFor(() => expect(api.worldCupDataSourcePreview).toHaveBeenCalledWith("api_football"));
    expect(await screen.findByText("API-Football preview")).toBeInTheDocument();
    expect(screen.getAllByText("converted").length).toBeGreaterThan(0);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("https://api-football.example/v3/statistics")).toBeInTheDocument();
    expect(screen.getByText("#2 invalid fact")).toBeInTheDocument();
  });

  it("imports with replace when the operator toggles replace import", async () => {
    render(<WorldCupDataSources />);

    await screen.findByText("Bundle URL");
    await userEvent.click(screen.getByLabelText("替换导入"));
    await userEvent.click(screen.getByRole("button", { name: "Bundle URL 导入" }));

    await waitFor(() => expect(api.worldCupDataSourceImport).toHaveBeenCalledWith("bundle_url", true));
  });

  it("runs World Cup resolution dry-run from the data-source panel", async () => {
    render(<WorldCupDataSources />);

    await screen.findByText("世界杯数据源");
    await userEvent.click(screen.getByRole("button", { name: "dry-run" }));

    await waitFor(() => expect(api.worldCupResolveDryRun).toHaveBeenCalledWith(200));
    expect(await screen.findByText("结算 dry-run")).toBeInTheDocument();
    expect(screen.getByText("Will Mexico reach the knockout stage?")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });
});

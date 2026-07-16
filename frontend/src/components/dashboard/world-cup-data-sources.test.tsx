import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { eventsApi } from "@/lib/api";
import { analyticsApi } from "@/lib/world-cup/analytics-api";
import { WorldCupDataSources } from "./world-cup-data-sources";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    worldCupDataSourcesStatus: vi.fn(),
    worldCupDataSourcePreview: vi.fn(),
    worldCupDataSourceImport: vi.fn(),
    worldCupResolveDryRun: vi.fn(),
    worldCupApiFootballTest: vi.fn(),
    worldCupApiFootballValidate: vi.fn(),
  },
}));

vi.mock("@/lib/world-cup/analytics-api", () => ({
  analyticsApi: {
    predictionCoverage: vi.fn(),
  },
}));

const api = eventsApi as unknown as {
  worldCupDataSourcesStatus: Mock;
  worldCupDataSourcePreview: Mock;
  worldCupDataSourceImport: Mock;
  worldCupResolveDryRun: Mock;
  worldCupApiFootballTest: Mock;
  worldCupApiFootballValidate: Mock;
};
const analytics = analyticsApi as unknown as {
  predictionCoverage: Mock;
};

const status = {
  facts: {
    count: 4,
    by_kind: { match_result: 2, qualification: 1, team_stat: 1 },
    last_updated: "2026-06-23T00:00:00Z",
  },
  real_data_readiness: {
    ok: true,
    qualification_source_configured: true,
    qualification_source_state: "ready",
    qualification_fact_count: 1,
    match_result_count: 2,
    recommended_qualification_import_mode: "api_football",
    recommended_qualification_import_label: "API-Football",
    scheduled_import_enabled: true,
    last_import_failed: false,
    issues: [],
    issue_details: [],
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
    api.worldCupApiFootballTest.mockReset();
    api.worldCupApiFootballValidate.mockReset();
    analytics.predictionCoverage.mockReset();
    api.worldCupDataSourcesStatus.mockResolvedValue(status);
    analytics.predictionCoverage.mockResolvedValue({
      status: "ok",
      coverage_ok: false,
      scheduled_count: 5,
      predicted_count: 4,
      missing_count: 1,
      stale_count: 1,
      stale_after_hours: 24,
      missing_predictions: [
        {
          match_id: "fd-1",
          home_team: "Spain",
          away_team: "Belgium",
          kickoff_utc: "2026-07-10T19:00:00Z",
          stage: "quarterfinal",
          status: "scheduled",
        },
      ],
      stale_predictions: [
        {
          match_id: "fd-2",
          home_team: "Norway",
          away_team: "England",
          kickoff_utc: "2026-07-11T21:00:00Z",
          stage: "quarterfinal",
          status: "scheduled",
          prediction_method: "hybrid",
          age_hours: 31.5,
        },
      ],
    });
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
    api.worldCupApiFootballValidate.mockResolvedValue({
      ok: false,
      error: "API-Football returned 0 fixtures for league=1 season=2026; check provider coverage/config before import.",
      steps: [
        {
          name: "connection",
          ok: true,
          detail: { requests_today: 7, requests_limit: 100 },
        },
        {
          name: "fixture_fetch",
          ok: false,
          fixture_count: 0,
          fixture_ids_sample: [],
          error: "API-Football returned 0 fixtures for league=1 season=2026; check provider coverage/config before import.",
        },
      ],
    });
  });

  it("renders configured source status and last run metrics", async () => {
    render(<WorldCupDataSources />);

    expect(await screen.findByText("facts 4")).toBeInTheDocument();
    expect(screen.getAllByText("API-Football").length).toBeGreaterThan(0);
    expect(screen.getByText("budget 25")).toBeInTheDocument();
    expect(screen.getByText("https://api-football.example/v3")).toBeInTheDocument();
    expect(screen.getByText("https://example.com/matches")).toBeInTheDocument();
    expect(screen.getByText("https://sportmonks.example/fixtures")).toBeInTheDocument();
    expect(screen.getByText("match_result")).toBeInTheDocument();
    expect(screen.getByText("Qualification facts")).toBeInTheDocument();
    expect(screen.getByText("1 imported")).toBeInTheDocument();
    expect(screen.getByText("Real qualification source")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("最近定时导入")).toBeInTheDocument();
    expect(screen.getByText("https://api-football.example/v3/fixtures")).toBeInTheDocument();
  });

  it("renders Football-Data.org as a first-class import source", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      real_data_readiness: {
        ...status.real_data_readiness,
        recommended_qualification_import_mode: "football_data",
        recommended_qualification_import_label: "Football-Data.org",
      },
      configured_sources: {
        ...status.configured_sources,
        football_data: {
          configured: true,
          base_url: "https://api.football-data.org/v4",
          competition: "WC",
        },
      },
      scheduled_import: {
        ...status.scheduled_import,
        mode: "football_data",
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("competition WC")).toBeInTheDocument();
    expect(screen.getAllByText("Football-Data.org").length).toBeGreaterThan(0);
    expect(screen.getByText("https://api.football-data.org/v4")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Football-Data.org 预览" }));
    await waitFor(() => expect(api.worldCupDataSourcePreview).toHaveBeenCalledWith("football_data"));
  });

  it("treats Football-Data.org as a direct standings import without API-Football validation copy", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      facts: {
        ...status.facts,
        by_kind: { match_result: 94 },
      },
      real_data_readiness: {
        ok: false,
        qualification_source_configured: true,
        qualification_source_state: "configured_not_imported",
        qualification_fact_count: 0,
        match_result_count: 94,
        recommended_qualification_import_mode: "football_data",
        recommended_qualification_import_label: "Football-Data.org",
        scheduled_import_enabled: true,
        last_import_failed: false,
        issues: ["qualification_import_required"],
        issue_details: [],
      },
      configured_sources: {
        ...status.configured_sources,
        football_data: {
          configured: true,
          base_url: "https://api.football-data.org/v4",
          competition: "WC",
        },
      },
      scheduled_import: {
        ...status.scheduled_import,
        mode: "football_data",
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("Recommended import")).toBeInTheDocument();
    expect(screen.getAllByText("Football-Data.org").length).toBeGreaterThan(0);
    expect(screen.getByText("直接导入 Football-Data.org 真实积分榜。")).toBeInTheDocument();
    expect(screen.queryByText("Run pipeline validation first; import only after it passes.")).not.toBeInTheDocument();
  });

  it("warns when no real qualification source is configured", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      facts: {
        ...status.facts,
        by_kind: { match_result: 94 },
      },
      real_data_readiness: {
        ok: false,
        qualification_source_configured: false,
        qualification_source_state: "not_configured",
        qualification_fact_count: 0,
        match_result_count: 94,
        recommended_qualification_import_mode: "",
        recommended_qualification_import_label: "",
        scheduled_import_enabled: false,
        last_import_failed: true,
        issues: [
          "qualification_source_not_configured",
          "qualification_facts_missing",
          "scheduled_import_disabled",
          "last_import_failed",
        ],
        issue_details: [
          {
            code: "qualification_source_not_configured",
            severity: "error",
            message: "尚未配置真实积分榜/出线数据源",
            action: "配置 WORLD_CUP_STANDINGS_SOURCE_URL，或配置 API-Football/SportMonks standings provider。",
          },
          {
            code: "qualification_facts_missing",
            severity: "error",
            message: "尚未导入真实出线/淘汰事实",
            action: "从真实 standings 源导入 qualification facts 后再信任出线状态。",
          },
        ],
      },
      configured_sources: {
        ...status.configured_sources,
        feeds: [
          { kind: "matches", configured: true, source_url: "https://example.com/matches" },
          { kind: "standings", configured: false, source_url: "" },
        ],
        api_football: {
          ...status.configured_sources.api_football,
          configured: false,
        },
        sportmonks: {
          configured: false,
          feeds: [
            { kind: "matches", configured: false, source_url: "" },
            { kind: "standings", configured: false, source_url: "" },
          ],
        },
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("https://example.com/matches")).toBeInTheDocument();
    expect(screen.getByText("0 imported")).toBeInTheDocument();
    expect(screen.getByText("Real qualification source")).toBeInTheDocument();
    expect(screen.getByText("Not configured: standings/API provider required")).toBeInTheDocument();
    expect(screen.getByText("尚未导入真实出线/淘汰事实")).toBeInTheDocument();
    expect(screen.getByText("从真实 standings 源导入 qualification facts 后再信任出线状态。")).toBeInTheDocument();
  });

  it("shows the recommended import when a qualification source is configured but facts are missing", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      facts: {
        ...status.facts,
        by_kind: { match_result: 94 },
      },
      real_data_readiness: {
        ok: false,
        qualification_source_configured: true,
        qualification_source_state: "configured_not_imported",
        qualification_fact_count: 0,
        match_result_count: 94,
        recommended_qualification_import_mode: "api_football",
        recommended_qualification_import_label: "API-Football",
        scheduled_import_enabled: false,
        last_import_failed: true,
        issues: [
          "qualification_facts_missing",
          "qualification_import_required",
          "scheduled_import_disabled",
          "last_import_failed",
        ],
        issue_details: [
          {
            code: "qualification_import_required",
            severity: "error",
            message: "Configured source has no imported qualification facts",
            action: "Run Import for the recommended source.",
          },
        ],
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("Real qualification source")).toBeInTheDocument();
    expect(await screen.findByText("Configured, import required")).toBeInTheDocument();
    expect(screen.getByText("Recommended import")).toBeInTheDocument();
    expect(screen.getByText("Run pipeline validation first; import only after it passes.")).toBeInTheDocument();
    expect(screen.getAllByText("API-Football").length).toBeGreaterThan(0);
    expect(screen.getAllByText("api_football").length).toBeGreaterThan(0);
    expect(screen.getByText("0 imported")).toBeInTheDocument();
  });

  it("shows the last failed recommended provider validation in readiness", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      facts: {
        ...status.facts,
        by_kind: { match_result: 94 },
      },
      real_data_readiness: {
        ok: false,
        qualification_source_configured: true,
        qualification_source_state: "validation_failed",
        qualification_fact_count: 0,
        match_result_count: 94,
        recommended_qualification_import_mode: "api_football",
        recommended_qualification_import_label: "API-Football",
        scheduled_import_enabled: false,
        last_import_failed: false,
        recommended_provider_last_validation_status: "failed",
        recommended_provider_last_validation_error: "API-Football returned 0 fixtures for league=1 season=2026",
        issues: [
          "qualification_facts_missing",
          "recommended_provider_validation_failed",
        ],
        issue_details: [
          {
            code: "recommended_provider_validation_failed",
            severity: "error",
            message: "Recommended provider validation failed",
            action: "API-Football pipeline validation failed; fix provider coverage/config before import.",
          },
        ],
      },
      runs: {
        ...status.runs,
        world_cup_api_football_validate: {
          status: "failed",
          started_at: "2026-07-06T05:20:00Z",
          finished_at: "2026-07-06T05:20:01Z",
          duration_ms: 1000,
          error: "API-Football returned 0 fixtures for league=1 season=2026",
          result: {
            provider: "api_football",
            fixture_count: 0,
            failed_step: "fixture_fetch",
          },
        },
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("Validation failed")).toBeInTheDocument();
    expect(screen.getByText("Last provider validation")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("API-Football returned 0 fixtures for league=1 season=2026")).toBeInTheDocument();
    expect(screen.getByText("failed_step")).toBeInTheDocument();
    expect(screen.getByText("fixture_fetch")).toBeInTheDocument();
  });

  it("shows untrusted qualification facts when source metadata is missing", async () => {
    api.worldCupDataSourcesStatus.mockResolvedValue({
      ...status,
      real_data_readiness: {
        ok: false,
        qualification_source_configured: true,
        qualification_source_state: "configured_not_imported",
        qualification_fact_count: 0,
        untrusted_qualification_fact_count: 1,
        match_result_count: 94,
        recommended_qualification_import_mode: "feeds",
        recommended_qualification_import_label: "Configured feeds",
        scheduled_import_enabled: true,
        last_import_failed: false,
        issues: ["qualification_facts_untrusted"],
        issue_details: [
          {
            code: "qualification_facts_untrusted",
            severity: "error",
            message: "Qualification facts exist but lack trusted source metadata",
            action: "Re-import qualification facts from a real standings source with source_url and observed_at.",
          },
        ],
      },
    });

    render(<WorldCupDataSources />);

    expect(await screen.findByText("trusted 0")).toBeInTheDocument();
    expect(await screen.findByText(/untrusted\s+1/)).toBeInTheDocument();
    expect(screen.getByText("Qualification facts exist but lack trusted source metadata")).toBeInTheDocument();
  });

  it("shows scheduled-match prediction coverage from analytics", async () => {
    render(<WorldCupDataSources />);

    expect(await screen.findByText("Prediction coverage")).toBeInTheDocument();
    expect(analytics.predictionCoverage).toHaveBeenCalledWith(24);
    expect(screen.getByText("scheduled 5")).toBeInTheDocument();
    expect(screen.getByText("predicted 4")).toBeInTheDocument();
    expect(screen.getByText("missing 1")).toBeInTheDocument();
    expect(screen.getByText("stale 1")).toBeInTheDocument();
    expect(screen.getByText("Spain vs Belgium")).toBeInTheDocument();
    expect(screen.getByText(/Norway vs England/)).toBeInTheDocument();
  });

  it("shows API-Football pipeline validation failure details", async () => {
    const statusAfterValidation = {
      ...status,
      real_data_readiness: {
        ...status.real_data_readiness,
        ok: false,
        qualification_source_state: "validation_failed",
        qualification_fact_count: 0,
        recommended_provider_last_validation_status: "failed",
        recommended_provider_last_validation_error: "API-Football returned 0 fixtures for league=1 season=2026",
        issues: ["recommended_provider_validation_failed"],
        issue_details: [
          {
            code: "recommended_provider_validation_failed",
            severity: "error",
            message: "Recommended provider validation failed",
            action: "API-Football pipeline validation failed; fix provider coverage/config before import.",
          },
        ],
      },
      runs: {
        ...status.runs,
        world_cup_api_football_validate: {
          status: "failed",
          started_at: "2026-07-06T05:20:00Z",
          finished_at: "2026-07-06T05:20:01Z",
          duration_ms: 1000,
          error: "API-Football returned 0 fixtures for league=1 season=2026",
          result: {
            provider: "api_football",
            fixture_count: 0,
            failed_step: "fixture_fetch",
          },
        },
      },
    };
    api.worldCupDataSourcesStatus
      .mockResolvedValueOnce(status)
      .mockResolvedValueOnce(statusAfterValidation);
    render(<WorldCupDataSources />);

    await screen.findByText("API-Football");
    await userEvent.click(screen.getByRole("button", { name: /Pipeline/ }));

    await waitFor(() => expect(api.worldCupApiFootballValidate).toHaveBeenCalled());
    expect((await screen.findAllByText(/API-Football returned 0 fixtures/)).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("fixture_count: 0")).toBeInTheDocument();
    await waitFor(() => expect(api.worldCupDataSourcesStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Last provider validation")).toBeInTheDocument();
    expect(screen.getByText("failed_step")).toBeInTheDocument();
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

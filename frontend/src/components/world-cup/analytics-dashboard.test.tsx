import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnalyticsDashboard } from "./analytics-dashboard";

function qualitySummary(samples = 2) {
  return {
    samples,
    outcome_accuracy: 0.5,
    exact_score_rate: 0,
    avg_score_mae: 1,
    avg_brier_score: 0.685,
    avg_log_loss: 1.1269,
    avg_confidence: 0.7,
    confidence_bias: 0.2,
    expected_calibration_error: 0.2,
    is_calibratable: false,
    calibration_buckets: [
      { label: "0-20%", count: 0, avg_confidence: null, accuracy: null, gap: null, is_usable: false },
      { label: "20-40%", count: 0, avg_confidence: null, accuracy: null, gap: null, is_usable: false },
      { label: "40-60%", count: 0, avg_confidence: null, accuracy: null, gap: null, is_usable: false },
      { label: "60-80%", count: samples, avg_confidence: 0.7, accuracy: 0.5, gap: 0.2, is_usable: false },
      { label: "80-100%", count: 0, avg_confidence: null, accuracy: null, gap: null, is_usable: false },
    ],
  };
}

function responseFor(url: string) {
  if (url.includes("/verified-result-correction")) {
    return {
      status: "ok",
      fixture: { status: "finished", score: { home: 0, away: 0 } },
      fact: {
        winner: "Switzerland",
        penalty_score: { home: 4, away: 3 },
      },
      fact_import: { imported: 1 },
    };
  }
  if (url.includes("/result-fact-backfill/runs")) {
    return {
      status: "ok",
      job_name: "world_cup_result_fact_backfill",
      count: 1,
      runs: [
        {
          id: "fact-import-prev",
          status: "success",
          started_at: "2026-06-26T12:30:00+00:00",
          finished_at: "2026-06-26T12:30:00+00:00",
          duration_ms: 88,
          error: null,
          dry_run: false,
          confirm: true,
          protected: false,
          finished_fixture_count: 60,
          existing_fact_matches: 0,
          candidate_count: 2,
          imported: 2,
          audit_metadata: { trigger_source: "world-cup-dashboard", operator: "alice" },
        },
      ],
    };
  }
  if (url.includes("/reconcile-scoring/runs")) {
    return {
      status: "ok",
      job_name: "world_cup_scoring_reconcile",
      count: 0,
      runs: [],
    };
  }
  if (url.includes("/result-fact-backfill")) {
    const dryRun = !url.includes("dry_run=false");
    return {
      status: "ok",
      dry_run: dryRun,
      confirm: !dryRun,
      protected: false,
      finished_fixture_count: 60,
      existing_fact_matches: 0,
      candidate_count: 2,
      imported: dryRun ? 0 : 2,
      skipped_existing: 0,
      run_id: dryRun ? undefined : "result-fact-write-1",
      items: [
        {
          match_id: "m-score",
          fixture_id: "m-score",
          home_team: "Argentina",
          away_team: "Brazil",
          score: { home: 2, away: 1 },
          action: dryRun ? "would_import" : "imported",
          fact: {
            fact_id: "wc2026:prediction-fixture-result:m-score",
            kind: "match_result",
            tournament: "2026 FIFA World Cup",
            match_id: "m-score",
            home_team: "Argentina",
            away_team: "Brazil",
            status: "finished",
            score: { home: 2, away: 1 },
            source: "prediction_fixture_db",
            confidence: 0.8,
            observed_at: "2026-06-20T21:00:00Z",
          },
        },
        {
          match_id: "m-missing",
          fixture_id: "m-missing",
          home_team: "France",
          away_team: "Germany",
          score: { home: 0, away: 0 },
          action: dryRun ? "would_import" : "imported",
          fact: {
            fact_id: "wc2026:prediction-fixture-result:m-missing",
            kind: "match_result",
            tournament: "2026 FIFA World Cup",
            match_id: "m-missing",
            home_team: "France",
            away_team: "Germany",
            status: "finished",
            score: { home: 0, away: 0 },
            source: "prediction_fixture_db",
            confidence: 0.8,
            observed_at: "2026-06-21T21:00:00Z",
          },
        },
      ],
    };
  }
  if (url.includes("/result-consistency")) {
    return {
      status: "ok",
      dry_run: true,
      generated_at: "2026-06-26T12:00:00+00:00",
      source: "stored_sports_facts",
      fact_store: {
        configured_path: "E:\\Github\\Prediction Market Reality Filter\\backend\\sports_facts.json",
        exists: false,
        count: 0,
        by_kind: {},
        updated_at: "",
        file_mtime: null,
      },
      fact_count: 3,
      fixture_count: 2,
      checked: 1,
      issue_count: 2,
      returned_issue_count: 2,
      issues: [
        {
          type: "score_mismatch",
          severity: "error",
          match_id: "m-score",
          fact: {
            fact_id: "fact-score",
            source: "test",
            observed_at: "2026-06-20T21:00:00Z",
            status: "finished",
            score: { home: 2, away: 1 },
          },
          fixture: {
            match_id: "m-score",
            fixture_id: "m-score",
            home_team: "Argentina",
            away_team: "Brazil",
            status: "finished",
            score: { home: 1, away: 1 },
            updated_at: "2026-06-20T20:59:00Z",
          },
          message: "Result fact score is 2-1, but prediction fixture score is 1-1.",
        },
        {
          type: "result_fact_missing_for_finished_fixture",
          severity: "warn",
          match_id: "m-missing",
          fact: null,
          fixture: {
            match_id: "m-missing",
            fixture_id: "m-missing",
            home_team: "France",
            away_team: "Germany",
            status: "finished",
            score: { home: 0, away: 0 },
            updated_at: "2026-06-21T20:59:00Z",
          },
          message: "Prediction fixture is finished, but no result fact was found.",
        },
      ],
    };
  }
  if (url.includes("/post-match-backfill/runs")) {
    return {
      status: "ok",
      job_name: "world_cup_post_match_backfill",
      count: 2,
      runs: [
        {
          id: "audit-dry-run-prev",
          status: "success",
          started_at: "2026-06-26T11:30:00+00:00",
          finished_at: "2026-06-26T11:30:00+00:00",
          duration_ms: 123,
          error: null,
          dry_run: true,
          source: "football-data",
          sync_status: "skipped",
          candidate_count: 2,
          scored: 0,
          skipped: 45,
          errors: 0,
          quality_samples: 2,
          audit_metadata: { trigger_source: "world-cup-dashboard", operator: "alice" },
        },
        {
          id: "audit-failed-prev",
          status: "failed",
          started_at: "2026-06-26T10:30:00+00:00",
          finished_at: "2026-06-26T10:30:01+00:00",
          duration_ms: 1000,
          error: "source unavailable",
          dry_run: false,
          source: "football-data",
          sync_status: "error",
          candidate_count: 0,
          stale_unfinished_count: 1,
          stale_unfinished_fixtures: [
            {
              match_id: "stale-r16",
              home_team: "Switzerland",
              away_team: "Colombia",
              stage: "ROUND_OF_16",
              kickoff_utc: "2026-07-07T20:00:00",
              status: "scheduled",
              home_score: null,
              away_score: null,
            },
          ],
          scored: 0,
          skipped: 0,
          errors: 1,
          quality_samples: null,
          audit_metadata: { trigger_source: "scheduled-worker" },
        },
      ],
    };
  }
  if (url.includes("/post-match-backfill")) {
    const dryRun = !url.includes("dry_run=false");
    return {
      status: "ok",
      run_id: dryRun ? "audit-dry-run-1" : "audit-write-1",
      dry_run: dryRun,
      source: "football-data",
      candidate_count: dryRun ? 2 : 2,
      candidates: [
        {
          match_id: "m1",
          home_team: "Argentina",
          away_team: "Brazil",
          kickoff_utc: "2026-06-14T18:00:00",
          actual_score: { home: 2, away: 1 },
        },
        {
          match_id: "m2",
          home_team: "France",
          away_team: "Germany",
          kickoff_utc: "2026-06-15T18:00:00",
          actual_score: { home: 1, away: 1 },
        },
      ],
      scoring: { scored: dryRun ? 0 : 2, skipped: dryRun ? 2 : 0, errors: 0 },
      quality: {
        samples: dryRun ? 2 : 4,
        outcome_accuracy: 0.5,
        avg_brier_score: 0.685,
        expected_calibration_error: 0.2,
        trend_days: 2,
        consistency_issues: 1,
      },
    };
  }
  if (url.includes("/consistency-repair-plan")) {
    return {
      status: "ok",
      dry_run: true,
      issue_count: 1,
      auto_fixable: 0,
      manual_review: 1,
      items: [
        {
          match_id: "m1",
          engine: "hybrid",
          timestamp: "2026-06-14T17:00:00",
          history_ids: [12],
          methods: ["unknown"],
          variant_count: 2,
          can_autofix: false,
          recommended_action: "manual_review_unknown_method",
          rationale: "历史行缺少 prediction_method，无法安全判断这些分数是否来自不同引擎。",
        },
      ],
    };
  }
  if (url.includes("/consistency-repair-preview")) {
    return {
      status: "ok",
      dry_run: true,
      requested: 1,
      inferable: 1,
      manual_review: 0,
      items: [
        {
          history_id: 12,
          status: "ok",
          match_id: "m1",
          timestamp: "2026-06-14T17:00:00",
          predicted_score: { home: 1, away: 2 },
          current_method: null,
          inferred_method: "rule_only",
          can_apply: true,
          reason: "same_match_same_score_known_method",
          source_history_ids: [42],
        },
      ],
    };
  }
  if (url.includes("/consistency-repair?")) {
    const dryRun = !url.includes("dry_run=false");
    return {
      status: "ok",
      dry_run: dryRun,
      confirm: !dryRun,
      protected: false,
      requested: 1,
      inferable: 1,
      updated: dryRun ? 0 : 1,
      skipped: 0,
      manual_review: 0,
      run_id: dryRun ? "repair-dry-run-1" : "repair-write-1",
      items: [
        {
          history_id: 12,
          status: "ok",
          match_id: "m1",
          timestamp: "2026-06-14T17:00:00",
          predicted_score: { home: 1, away: 2 },
          current_method: null,
          inferred_method: "rule_only",
          can_apply: true,
          reason: "same_match_same_score_known_method",
          source_history_ids: [42],
          action: dryRun ? "would_update" : "updated",
          applied_method: dryRun ? null : "rule_only",
        },
      ],
    };
  }
  if (url.includes("/engine-stats")) {
    return {
      total_predictions: 3,
      by_engine: {
        elo_odds: { count: 1, percentage: 33.3, avg_confidence: 0.7 },
        hybrid: { count: 1, percentage: 33.3, avg_confidence: 0.7 },
        integrated: { count: 1, percentage: 33.3, avg_confidence: 0.7 },
      },
    };
  }
  if (url.includes("/accuracy-stats")) {
    return {
      total_matches: 2,
      outcome_accuracy: 0.5,
      avg_score_mae: 1,
      avg_brier_score: 0.685,
      exact_score_correct: 0,
    };
  }
  if (url.includes("/odds-cache-stats")) {
    return {
      total_entries: 1,
      fresh_count: 1,
      stale_count: 0,
      estimated_api_calls_saved: 1,
      cache_hit_rate: 1,
    };
  }
  if (url.includes("/system-health")) {
    return {
      status: "healthy",
      recent_predictions_24h: 3,
      cache_entries: 1,
      data_freshness_hours: 0.5,
      last_update: "2026-06-15T12:00:00Z",
    };
  }
  return {
    status: "ok",
    sample_policy: "latest applied pre-match prediction per engine per finished match",
    counters: {
      finished_matches: 2,
      matches_without_history: 0,
      history_rows_excluded_after_kickoff: 0,
      history_rows_excluded_comparison: 0,
    },
    overall: qualitySummary(2),
    by_engine: {
      elo_odds: qualitySummary(1),
      hybrid: qualitySummary(1),
      integrated: qualitySummary(0),
    },
    trends: {
      overall: [
        {
          date: "2026-06-14",
          samples: 1,
          outcome_accuracy: 1,
          avg_brier_score: 0.135,
          avg_log_loss: 0.3567,
          expected_calibration_error: 0.2,
        },
        {
          date: "2026-06-15",
          samples: 1,
          outcome_accuracy: 0,
          avg_brier_score: 1.235,
          avg_log_loss: 1.8971,
          expected_calibration_error: 0.6,
        },
      ],
      by_engine: {
        elo_odds: [],
        hybrid: [],
        integrated: [],
      },
    },
    consistency_issues: [
      {
        type: "conflicting_same_timestamp_score",
        severity: "warn",
        match_id: "m1",
        engine: "unknown",
        timestamp: "2026-06-14T17:00:00",
        rows: 2,
        variant_count: 2,
        variants: [
          { predicted_score: { home: 2, away: 1 }, count: 1, history_ids: [11], triggers: ["manual"], methods: ["unknown"] },
          { predicted_score: { home: 1, away: 2 }, count: 1, history_ids: [12], triggers: ["manual"], methods: ["unknown"] },
        ],
        has_unknown_method: true,
        message: "同一场、同一时间、同一引擎出现不同预测比分。",
      },
    ],
    integrated_weight_suggestion: {
      elo_weight: 0.7,
      hybrid_weight: 0.3,
      source: "rule_default",
      reason: "insufficient_component_samples",
      samples: { elo_odds: 1, hybrid: 1 },
      brier: { elo_odds: 1.235, hybrid: 0.135 },
    },
    recommendations: [{ level: "warn", title: "样本不足", message: "继续累计样本。" }],
  };
}

describe("AnalyticsDashboard", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("renders quality trend charts from the quality-loop response", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => responseFor(String(input)),
    }));
    vi.stubGlobal(
      "fetch",
      fetchMock,
    );
    window.sessionStorage.setItem("pmrf.operatorApiKey", "secret");

    render(<AnalyticsDashboard />);

    expect(await screen.findByText("历史趋势")).toBeInTheDocument();
    expect(screen.getByText("2 天")).toBeInTheDocument();
    expect(screen.getAllByText("ECE").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("LogLoss")).toBeInTheDocument();
    expect(screen.getByText("数据一致性问题")).toBeInTheDocument();
    expect(screen.getByText(/m1.*未知方法/)).toBeInTheDocument();
    expect(screen.getByText("缺失方法的历史行同一秒出现不同预测比分。")).toBeInTheDocument();
    expect(screen.getByText("2-1 / 1-2")).toBeInTheDocument();
    expect(screen.getAllByText(/方法 未知方法/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/行 11/)).toBeInTheDocument();
    expect(screen.getByText("修复建议")).toBeInTheDocument();
    expect(screen.getByText("自动 0 / 人工 1")).toBeInTheDocument();
    expect(screen.getByText(/历史行缺少 prediction_method/)).toBeInTheDocument();
    expect(await screen.findByText("预览")).toBeInTheDocument();
    expect(screen.getByText("可推断 1 / 1")).toBeInTheDocument();
    expect(screen.getByText("rule_only")).toBeInTheDocument();
    expect(screen.getByText("比分一致性")).toBeInTheDocument();
    expect(screen.getByText("待检查")).toBeInTheDocument();
    expect(screen.getByText("事实存储")).toBeInTheDocument();
    expect(screen.getByText("缺少事实文件")).toBeInTheDocument();
    expect(screen.getByText(/来源\s*stored_sports_facts/)).toBeInTheDocument();
    expect(screen.getByText(/比赛结果\s*0/)).toBeInTheDocument();
    expect(screen.getByText("比分事实回填")).toBeInTheDocument();
    expect(await screen.findByText("最近事实导入")).toBeInTheDocument();
    expect(screen.getByText("fact-import-prev")).toBeInTheDocument();
    expect(screen.getByText("耗时 88ms")).toBeInTheDocument();
    expect(screen.getAllByText("via world-cup-dashboard / alice").length).toBeGreaterThanOrEqual(1);
    const factWriteButton = screen.getByRole("button", { name: /导入事实/ });
    expect(factWriteButton).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^试运行$/ }));
    expect(await screen.findByText("回填结果")).toBeInTheDocument();
    expect(screen.getAllByText(/候选\s*2/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/已导入\s*0/)).toBeInTheDocument();
    expect(screen.getByText("Argentina vs Brazil")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/result-fact-backfill?"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("dry_run=true"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
    });
    fireEvent.click(screen.getByLabelText("确认事实写入"));
    expect(factWriteButton).not.toBeDisabled();
    fireEvent.click(factWriteButton);
    expect(await screen.findByText("result-fact-write-1")).toBeInTheDocument();
    expect(screen.getAllByText(/已导入\s*2/).length).toBeGreaterThanOrEqual(1);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("dry_run=false"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("confirm=true"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/result-fact-backfill/runs?limit=5"),
        expect.objectContaining({ cache: "no-store" }),
      );
    });
    expect(screen.getAllByText("Score mismatch").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Fact missing").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("m-score")).toBeInTheDocument();
    expect(screen.getByText("m-missing")).toBeInTheDocument();
    expect(screen.getByText(/Result fact score is 2-1/)).toBeInTheDocument();
    const repairWriteButton = screen.getByRole("button", { name: /应用修复/ });
    expect(repairWriteButton).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /修复试运行/ }));
    expect(await screen.findByText("repair-dry-run-1")).toBeInTheDocument();
    expect(screen.getByText(/已更新\s*0/)).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/consistency-repair?"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("dry_run=true"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
    });
    fireEvent.click(screen.getByLabelText("确认方法写入"));
    expect(repairWriteButton).not.toBeDisabled();
    fireEvent.click(repairWriteButton);
    expect(await screen.findByText("repair-write-1")).toBeInTheDocument();
    expect(screen.getByText(/已更新\s*1/)).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("dry_run=false"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("confirm=true"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
    });
    expect(screen.getByText(/来源 42/)).toBeInTheDocument();
    expect(screen.getByText("赛后回填")).toBeInTheDocument();
    expect((await screen.findAllByText("最近审计")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("audit-dry-run-prev")).toBeInTheDocument();
    expect(screen.getByText("audit-failed-prev")).toBeInTheDocument();
    expect(screen.getByText("source unavailable")).toBeInTheDocument();
    expect(screen.getByText("未完成赛果 1")).toBeInTheDocument();
    expect(screen.getAllByText("Switzerland vs Colombia").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("耗时 123ms")).toBeInTheDocument();
    expect(screen.getByText("跳过 45")).toBeInTheDocument();
    expect(screen.getByText("via scheduled-worker")).toBeInTheDocument();

    const writeButton = screen.getByRole("button", { name: /执行回填/ });
    expect(writeButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /检查回填/ }));

    expect(await screen.findByText("Argentina vs Brazil")).toBeInTheDocument();
    expect(screen.getAllByText("France vs Germany").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("2-1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("audit-dry-run-1")).toBeInTheDocument();
    expect(screen.getByText("跳过").parentElement).toHaveTextContent("2");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/post-match-backfill?dry_run=true"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/post-match-backfill/runs?limit=5"),
        expect.objectContaining({ cache: "no-store" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/consistency-repair-plan?limit=25"),
        expect.objectContaining({ method: "GET" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/consistency-repair-preview?history_ids=12"),
        expect.objectContaining({ method: "GET" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/result-consistency?limit=25"),
        expect.objectContaining({ method: "GET" }),
      );
    });

    fireEvent.click(screen.getByLabelText("确认写入评分"));
    expect(writeButton).not.toBeDisabled();
    fireEvent.click(writeButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/post-match-backfill?dry_run=false"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
    });
  });

  it("submits a verified stale knockout result correction with penalty winner provenance", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => responseFor(String(input)),
    }));
    vi.stubGlobal("fetch", fetchMock);
    window.sessionStorage.setItem("pmrf.operatorApiKey", "secret");

    render(<AnalyticsDashboard />);

    expect((await screen.findAllByText("Switzerland vs Colombia")).length).toBeGreaterThanOrEqual(1);

    fireEvent.change(screen.getByLabelText("Verified home score for stale-r16"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Verified away score for stale-r16"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Penalty home score for stale-r16"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Penalty away score for stale-r16"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Verified winner for stale-r16"), { target: { value: "Switzerland" } });
    fireEvent.change(screen.getByLabelText("Result source for stale-r16"), { target: { value: "Sky Sports" } });
    fireEvent.change(screen.getByLabelText("Result source URL for stale-r16"), {
      target: { value: "https://www.skysports.com/world-cup/switzerland-colombia" },
    });
    fireEvent.change(screen.getByLabelText("Result notes for stale-r16"), {
      target: { value: "0-0 after extra time; Switzerland won 4-3 on penalties." },
    });
    fireEvent.click(screen.getByLabelText("Confirm verified correction for stale-r16"));
    fireEvent.click(screen.getByRole("button", { name: "Submit verified correction for stale-r16" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/analytics/verified-result-correction"),
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            "X-API-Key": "secret",
            "X-Client-Source": "world-cup-dashboard",
          }),
        }),
      );
    });

    const correctionCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/verified-result-correction"));
    expect(correctionCall).toBeTruthy();
    const body = JSON.parse(String(correctionCall?.[1]?.body));
    expect(body).toEqual({
      match_id: "stale-r16",
      home_score: 0,
      away_score: 0,
      winner: "Switzerland",
      penalty_score: { home: 4, away: 3 },
      source: "Sky Sports",
      source_url: "https://www.skysports.com/world-cup/switzerland-colombia",
      notes: "0-0 after extra time; Switzerland won 4-3 on penalties.",
      confirmed: true,
    });
    expect(await screen.findByText("Verified correction saved for stale-r16")).toBeInTheDocument();
  });
});

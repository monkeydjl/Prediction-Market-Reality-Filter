"use client";

import { useState } from "react";
import Link from "next/link";
import {
  processOutcome,
  usePredictionHistory,
  type PredictionHistoryItem,
} from "@/lib/sports-api";
import { learningHistoryHref } from "@/lib/sports-routes";

const SPORT_FILTERS = [
  { value: "", label: "全部" },
  { value: "football", label: "Football" },
  { value: "basketball", label: "Basketball" },
  { value: "baseball", label: "Baseball" },
  { value: "hockey", label: "Hockey" },
];

const COMPETITION_OPTIONS = [
  { value: "", label: "全部" },
  { value: "wc", label: "wc" },
  { value: "ucl", label: "ucl" },
  { value: "epl", label: "epl" },
  { value: "laliga", label: "laliga" },
  { value: "bundesliga", label: "bundesliga" },
  { value: "seriea", label: "seriea" },
  { value: "ligue1", label: "ligue1" },
  { value: "nba", label: "nba" },
  { value: "mlb", label: "mlb" },
  { value: "nhl", label: "nhl" },
];

const PAGE_SIZE = 50;

function maxProb(probs: Record<string, number>): string {
  const values = Object.values(probs);
  if (values.length === 0) return "—";
  return `${(Math.max(...values) * 100).toFixed(1)}%`;
}

function resultBadge(item: PredictionHistoryItem): string {
  if (item.outcome === null) return "—";
  if (item.outcome.outcome_correct === null) return "待算";
  return item.outcome.outcome_correct ? "✓" : "✗";
}

/** A row is gradeable while its outcome has not been scored by the learning loop. */
function isUngraded(item: PredictionHistoryItem): boolean {
  return item.outcome === null || item.outcome.outcome_correct === null;
}

export function PredictionHistoryList() {
  const [offset, setOffset] = useState(0);
  const [sport, setSport] = useState("");
  const [competition, setCompetition] = useState("");
  const [processing, setProcessing] = useState<string | null>(null);
  const [processError, setProcessError] = useState<string | null>(null);
  const [processed, setProcessed] = useState<Set<string>>(new Set());

  const { data, error, isLoading, mutate } = usePredictionHistory({
    sport: sport || undefined,
    competition: competition || undefined,
    limit: PAGE_SIZE,
    offset,
  });
  const items: PredictionHistoryItem[] = data?.items ?? [];
  const total: number = data?.total ?? 0;

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  async function handleProcess(matchId: string) {
    setProcessing(matchId);
    setProcessError(null);
    try {
      await processOutcome(matchId);
      setProcessed((prev) => new Set(prev).add(matchId));
      await mutate();
    } catch (e) {
      setProcessError(e instanceof Error ? e.message : "结果回流失败");
    } finally {
      setProcessing(null);
    }
  }

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (error) {
    return <div className="p-4 text-sm text-neg">加载失败</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>运动</span>
          <select
            value={sport}
            onChange={(e) => { setSport(e.target.value); setOffset(0); }}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {SPORT_FILTERS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>赛事</span>
          <select
            value={competition}
            onChange={(e) => { setCompetition(e.target.value); setOffset(0); }}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {COMPETITION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      {processError && (
        <p
          data-testid="process-outcome-error"
          role="alert"
          className="rounded border border-neg/40 bg-neg/10 px-3 py-2 text-sm text-neg"
        >
          {processError}
        </p>
      )}

      {items.length === 0 ? (
        <div className="p-4 text-sm text-muted-foreground">暂无预测历史记录</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4">时间</th>
                  <th className="py-2 pr-4">Match ID</th>
                  <th className="py-2 pr-4">引擎</th>
                  <th className="py-2 pr-4">预测概率</th>
                  <th className="py-2 pr-4">置信度</th>
                  <th className="py-2 pr-4">结果</th>
                  <th className="py-2 pr-4">MAE</th>
                  <th className="py-2 pr-4">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-border/50 hover:bg-muted/30">
                    <td className="py-2 pr-4 text-muted-foreground">
                      {new Date(item.created_at).toLocaleString("zh-CN")}
                    </td>
                    <td className="py-2 pr-4">
                      <Link
                        href={learningHistoryHref(item.match_id)}
                        className="font-mono text-primary hover:underline"
                      >
                        {item.match_id.length > 20 ? `${item.match_id.slice(0, 18)}...` : item.match_id}
                      </Link>
                    </td>
                    <td className="py-2 pr-4 font-mono">{item.engine}</td>
                    <td className="py-2 pr-4 font-mono">{maxProb(item.outcome_probabilities)}</td>
                    <td className="py-2 pr-4 font-mono">{(item.confidence * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 font-mono">{resultBadge(item)}</td>
                    <td className="py-2 pr-4 font-mono">
                      {item.outcome?.score_mae?.toFixed(2) ?? "—"}
                    </td>
                    <td className="py-2 pr-4">
                      {isUngraded(item) ? (
                        <button
                          type="button"
                          data-testid={`process-outcome-${item.match_id}`}
                          onClick={() => handleProcess(item.match_id)}
                          disabled={processing !== null}
                          className="rounded border border-border px-2 py-0.5 text-xs hover:bg-muted disabled:opacity-40"
                        >
                          {processing === item.match_id ? "回流中…" : "回流结果"}
                        </button>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {processed.has(item.match_id) ? "已回流" : "—"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <span>第 {currentPage} / {totalPages} 页</span>
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              上一页
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}

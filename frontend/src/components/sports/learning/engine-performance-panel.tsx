"use client";

import { useMemo, useState } from "react";
import {
  useEngineScore,
  useEngineScores,
  useEnginesMeta,
  type EngineScoreItem,
} from "@/lib/sports-api";

const SPORT_OPTIONS = [
  { value: "", label: "全部" },
  { value: "football", label: "Football" },
  { value: "basketball", label: "Basketball" },
  { value: "baseball", label: "Baseball" },
  { value: "hockey", label: "Hockey" },
];

/**
 * Engine names are read from `/predictions/engines/meta` so the filter tracks
 * what the kernel actually has registered (the same vocabulary the engine
 * score table is keyed by). This list only covers the offline case.
 */
const FALLBACK_ENGINES = ["elo_odds", "basketball", "baseball", "hockey"];

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

function accuracyClass(acc: number): string {
  if (acc >= 0.70) return "text-green-400 font-medium";
  if (acc < 0.50) return "text-red-400";
  return "";
}

function brierClass(brier: number): string {
  if (brier <= 0.20) return "text-green-400 font-medium";
  if (brier > 0.30) return "text-red-400";
  return "";
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function EnginePerformancePanel() {
  const [engine, setEngine] = useState("");
  const [competition, setCompetition] = useState("");
  const [sport, setSport] = useState("");
  const { data, error, isLoading } = useEngineScores({
    engine: engine || undefined,
    competition: competition || undefined,
    sport: sport || undefined,
  });
  // Aggregate score for a single engine — the list route groups by competition,
  // so this is the only place the cross-competition roll-up is available.
  const { data: engineRollup } = useEngineScore(
    engine || null,
    competition || undefined,
  );
  const { data: enginesMeta } = useEnginesMeta();
  const engineOptions = useMemo(() => {
    const names = enginesMeta?.engines?.length
      ? enginesMeta.engines
      : FALLBACK_ENGINES;
    return [
      { value: "", label: "全部" },
      ...names.map((name) => ({ value: name, label: name })),
    ];
  }, [enginesMeta]);
  const rows: EngineScoreItem[] = data ?? [];
  const hasError = error !== undefined;

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (hasError) {
    return <div className="p-4 text-sm text-red-500">加载失败</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>引擎</span>
          <select
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {engineOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>赛事</span>
          <select
            value={competition}
            onChange={(e) => setCompetition(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {COMPETITION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span>运动</span>
          <select
            value={sport}
            onChange={(e) => setSport(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {SPORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      {engine && engineRollup && (
        <div
          data-testid="engine-rollup"
          className="rounded border border-border bg-muted/40 px-3 py-2 text-sm"
        >
          <span className="font-mono">{engineRollup.engine}</span> 跨赛事汇总：准确率{" "}
          <span className={accuracyClass(engineRollup.accuracy)}>
            {fmtPct(engineRollup.accuracy)}
          </span>
          {" · "}Brier{" "}
          <span className={brierClass(engineRollup.brier_score)}>
            {engineRollup.brier_score?.toFixed(3) ?? "—"}
          </span>
          {" · "}MAE {engineRollup.avg_mae?.toFixed(2) ?? "—"}
          {" · "}样本 {engineRollup.sample_count}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="p-4 text-sm text-muted-foreground">暂无性能数据，等待比赛结果录入</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-4">引擎</th>
                <th className="py-2 pr-4">赛事</th>
                <th className="py-2 pr-4">准确率</th>
                <th className="py-2 pr-4">MAE</th>
                <th className="py-2 pr-4">Brier</th>
                <th className="py-2 pr-4">校准度</th>
                <th className="py-2 pr-4">样本数</th>
                <th className="py-2 pr-4">更新时间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={`${row.engine}-${row.competition ?? "global"}-${i}`} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono">{row.engine}</td>
                  <td className="py-2 pr-4 font-mono">{row.competition ?? "全局"}</td>
                  <td className={`py-2 pr-4 font-mono ${accuracyClass(row.accuracy)}`}>{fmtPct(row.accuracy)}</td>
                  <td className="py-2 pr-4 font-mono">{row.avg_mae?.toFixed(2) ?? "—"}</td>
                  <td className={`py-2 pr-4 font-mono ${brierClass(row.brier_score)}`}>{row.brier_score?.toFixed(3) ?? "—"}</td>
                  <td className="py-2 pr-4 font-mono">{row.confidence_calibration?.toFixed(3) ?? "—"}</td>
                  <td className="py-2 pr-4 font-mono">{row.sample_count}</td>
                  <td className="py-2 pr-4 text-muted-foreground">
                    {row.last_updated ? new Date(row.last_updated).toLocaleString("zh-CN") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

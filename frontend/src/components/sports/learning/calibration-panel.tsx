"use client";

import { useState } from "react";
import {
  useCalibration,
  useReliability,
  type CalibrationItem,
  type ReliabilityData,
} from "@/lib/sports-api";
import { ReliabilityChart } from "./reliability-chart";

const ENGINE_OPTIONS = [
  { value: "", label: "全部" },
  { value: "elo_odds", label: "elo_odds" },
  { value: "basketball", label: "basketball" },
  { value: "baseball", label: "baseball" },
  { value: "hockey", label: "hockey" },
];

const COMPETITION_OPTIONS = [
  { value: "", label: "全部" },
  { value: "wc", label: "wc" },
  { value: "ucl", label: "ucl" },
  { value: "epl", label: "epl" },
  { value: "nba", label: "nba" },
  { value: "mlb", label: "mlb" },
  { value: "nhl", label: "nhl" },
];

export function CalibrationPanel() {
  const [engine, setEngine] = useState("");
  const [competition, setCompetition] = useState("");

  const params = { engine: engine || undefined, competition: competition || undefined };

  // Each hook fetches independently — one failure doesn't block the other,
  // matching the previous Promise.allSettled behavior.
  const cal = useCalibration(params);
  const rel = useReliability(params);

  const calibrations: CalibrationItem[] | null = cal.data ?? null;
  const calError = cal.error !== undefined;
  const calLoading = cal.isLoading;
  const reliability: ReliabilityData | null = rel.data ?? null;
  const relError = rel.error !== undefined;
  const relLoading = rel.isLoading;

  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground">
        Kernel 校准参数与可靠性图（按引擎/赛事）。与「历史复盘」里的事件 Brier 统计相互独立。
      </p>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span>引擎</span>
          <select
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {ENGINE_OPTIONS.map((o) => (
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
      </div>

      {/* Parameter table */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">校准参数</h2>
        {calError ? (
          <div className="p-4 text-sm text-red-500">校准数据加载失败</div>
        ) : calLoading || calibrations === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : calibrations.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground">暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4">引擎</th>
                  <th className="py-2 pr-4">赛事</th>
                  <th className="py-2 pr-4">斜率</th>
                  <th className="py-2 pr-4">截距</th>
                  <th className="py-2 pr-4">样本数</th>
                  <th className="py-2 pr-4">平均置信度</th>
                  <th className="py-2 pr-4">平均准确率</th>
                  <th className="py-2 pr-4">更新时间</th>
                </tr>
              </thead>
              <tbody>
                {calibrations.map((calItem, i) => (
                  <tr key={`${calItem.engine}-${calItem.competition}-${i}`} className="border-b border-border/50">
                    <td className="py-2 pr-4 font-mono">{calItem.engine}</td>
                    <td className="py-2 pr-4 font-mono">{calItem.competition}</td>
                    <td className="py-2 pr-4 font-mono">{calItem.slope.toFixed(2)}</td>
                    <td className="py-2 pr-4 font-mono">{calItem.intercept.toFixed(3)}</td>
                    <td className="py-2 pr-4 font-mono">{calItem.sample_count}</td>
                    <td className="py-2 pr-4 font-mono">{(calItem.avg_confidence * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 font-mono">{(calItem.avg_accuracy * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 text-muted-foreground">
                      {calItem.last_updated ? new Date(calItem.last_updated).toLocaleString("zh-CN") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Reliability chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">可靠性图</h2>
        {relError ? (
          <div className="p-4 text-sm text-red-500">可靠性数据加载失败</div>
        ) : relLoading || reliability === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : (
          <ReliabilityChart
            bins={reliability.bins}
            ece={reliability.ece}
            sampleCount={reliability.sample_count}
            maxCalibrationError={reliability.max_calibration_error}
          />
        )}
      </div>
    </div>
  );
}

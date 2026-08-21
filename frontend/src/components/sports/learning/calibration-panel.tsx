"use client";

import { useState } from "react";
import {
  useCalibration,
  useReliability,
  useConfidenceReliability,
  type CalibrationItem,
  type ReliabilityData,
  type ConfidenceReliabilityData,
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
  const conf = useConfidenceReliability(params);

  const calibrations: CalibrationItem[] | null = cal.data ?? null;
  const calError = cal.error !== undefined;
  const calLoading = cal.isLoading;
  const reliability: ReliabilityData | null = rel.data ?? null;
  const relError = rel.error !== undefined;
  const relLoading = rel.isLoading;
  const confidence: ConfidenceReliabilityData | null = conf.data ?? null;
  const confError = conf.error !== undefined;
  const confLoading = conf.isLoading;

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
          <div className="p-4 text-sm text-neg">校准数据加载失败</div>
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
          <div className="p-4 text-sm text-neg">可靠性数据加载失败</div>
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

      {/* Confidence reliability chart (P1-X1) */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">置信度可靠性图</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          上图的横轴是最高结果概率，这里是引擎自报的置信度——两者是不同的量。置信度公式的输出范围为
          0.30–0.95，因此最低与最高分桶为空属预期，并非缺失数据。
        </p>
        {confError ? (
          <div className="p-4 text-sm text-neg">置信度可靠性数据加载失败</div>
        ) : confLoading || confidence === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : (
          <>
            {confidence.signed_gap != null && (
              <div
                data-testid="confidence-signed-gap"
                className="mb-2 flex flex-wrap gap-3 text-xs text-muted-foreground"
              >
                <span
                  className={`rounded bg-secondary px-2 py-1 font-mono ${
                    confidence.signed_gap > 0
                      ? "text-neg"
                      : confidence.signed_gap < 0
                        ? "text-pos"
                        : ""
                  }`}
                >
                  {confidence.signed_gap > 0
                    ? "过度自信"
                    : confidence.signed_gap < 0
                      ? "保守"
                      : "一致"}{" "}
                  {confidence.signed_gap > 0 ? "+" : ""}
                  {(confidence.signed_gap * 100).toFixed(1)}pp
                </span>
                {confidence.mean_confidence != null && (
                  <span className="rounded bg-secondary px-2 py-1 font-mono">
                    平均置信度 {(confidence.mean_confidence * 100).toFixed(1)}%
                  </span>
                )}
                {confidence.mean_accuracy != null && (
                  <span className="rounded bg-secondary px-2 py-1 font-mono">
                    平均准确率 {(confidence.mean_accuracy * 100).toFixed(1)}%
                  </span>
                )}
              </div>
            )}
            <ReliabilityChart
              bins={confidence.bins}
              ece={confidence.ece}
              sampleCount={confidence.sample_count}
              maxCalibrationError={confidence.max_calibration_error}
            />
          </>
        )}
      </div>
    </div>
  );
}

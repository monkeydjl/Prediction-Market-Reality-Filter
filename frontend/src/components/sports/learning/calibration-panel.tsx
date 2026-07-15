"use client";

import { useEffect, useState } from "react";
import { fetchCalibration, fetchReliability, type CalibrationItem, type ReliabilityData } from "@/lib/learning-api";
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
  const [calibrations, setCalibrations] = useState<CalibrationItem[] | null>(null);
  const [reliability, setReliability] = useState<ReliabilityData | null>(null);
  const [calError, setCalError] = useState(false);
  const [relError, setRelError] = useState(false);
  const [engine, setEngine] = useState("");
  const [competition, setCompetition] = useState("");

  useEffect(() => {
    setCalError(false);
    setCalibrations(null);
    setRelError(false);
    setReliability(null);

    const params = { engine: engine || undefined, competition: competition || undefined };

    // Parallel requests with allSettled — one failure doesn't block the other
    Promise.allSettled([
      fetchCalibration(params),
      fetchReliability(params),
    ]).then(([calResult, relResult]) => {
      if (calResult.status === "fulfilled") {
        setCalibrations(calResult.value);
      } else {
        setCalError(true);
      }
      if (relResult.status === "fulfilled") {
        setReliability(relResult.value);
      } else {
        setRelError(true);
      }
    });
  }, [engine, competition]);

  return (
    <div className="space-y-6">
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
        ) : calibrations === null ? (
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
                {calibrations.map((cal, i) => (
                  <tr key={`${cal.engine}-${cal.competition}-${i}`} className="border-b border-border/50">
                    <td className="py-2 pr-4 font-mono">{cal.engine}</td>
                    <td className="py-2 pr-4 font-mono">{cal.competition}</td>
                    <td className="py-2 pr-4 font-mono">{cal.slope.toFixed(2)}</td>
                    <td className="py-2 pr-4 font-mono">{cal.intercept.toFixed(3)}</td>
                    <td className="py-2 pr-4 font-mono">{cal.sample_count}</td>
                    <td className="py-2 pr-4 font-mono">{(cal.avg_confidence * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 font-mono">{(cal.avg_accuracy * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4 text-muted-foreground">
                      {cal.last_updated ? new Date(cal.last_updated).toLocaleString("zh-CN") : "—"}
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
        ) : reliability === null ? (
          <div className="p-4 text-sm text-muted-foreground">加载中...</div>
        ) : (
          <ReliabilityChart bins={reliability.bins} />
        )}
      </div>
    </div>
  );
}

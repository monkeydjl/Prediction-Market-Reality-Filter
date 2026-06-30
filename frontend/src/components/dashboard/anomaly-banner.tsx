import { AlertTriangle, CheckCircle2 } from "lucide-react";
import type { QualityMetricsAnomaly } from "@/lib/api";

const SEVERITY_TONE: Record<string, string> = {
  high: "border-neg/40 bg-neg/10 text-neg",
  medium: "border-primary/40 bg-primary/10 text-primary",
  low: "border-border bg-muted text-muted-foreground",
};

const SEVERITY_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const CODE_LABEL: Record<string, string> = {
  scheduler_not_running: "调度器未运行",
  scheduler_job_failed: "调度任务失败",
  scheduler_zero_resolved: "调度成功但无新结算",
  calibration_brier_high: "Brier 分数过高",
  wide_spread_not_downgraded: "宽价差未降级",
  llm_degraded_mode_events: "LLM 降级模式事件",
  brier_relative_drift: "Brier 相对漂移",
  bucket_deviation: "桶偏差",
  degraded_mixing: "降级样本混入",
};

export function AnomalyBanner({ anomalies }: { anomalies: QualityMetricsAnomaly[] }) {
  if (anomalies.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
        <CheckCircle2 className="size-4 text-pos" aria-hidden="true" />
        <span>无异常 — 质量引擎运行正常</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {anomalies.map((a, i) => {
        const tone = SEVERITY_TONE[a.severity] ?? SEVERITY_TONE.low;
        const label = CODE_LABEL[a.code] ?? a.code;
        const detail = typeof a.detail === "string" ? a.detail : JSON.stringify(a.detail);
        return (
          <div
            key={`${a.code}-${i}`}
            className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${tone}`}
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="flex flex-col gap-0.5">
              <span className="font-medium">
                {label}
                <span className="ml-2 rounded px-1.5 py-0.5 text-xs bg-card/50">
                  {SEVERITY_LABEL[a.severity] ?? a.severity}
                </span>
              </span>
              <span className="text-xs opacity-80">{detail}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

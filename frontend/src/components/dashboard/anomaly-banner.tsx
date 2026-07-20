import Link from "next/link";
import { AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react";
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
  direction_accuracy_low: "方向准确率偏低",
  brier_score_high: "Brier 过高",
  missing_calibration_rate_high: "缺失校准率偏高",
  report_errors_high: "报告抽取错误偏多",
};

function extractEventIds(a: QualityMetricsAnomaly): string[] {
  if (Array.isArray(a.event_ids) && a.event_ids.length > 0) {
    return a.event_ids.filter((x): x is string => typeof x === "string" && x.length > 0);
  }
  if (a.detail && typeof a.detail === "object" && a.detail !== null) {
    const d = a.detail as { event_ids?: unknown };
    if (Array.isArray(d.event_ids)) {
      return d.event_ids.filter((x): x is string => typeof x === "string" && x.length > 0);
    }
  }
  return [];
}

function defaultHref(code: string): string | null {
  if (code === "calibration_brier_high" || code === "brier_score_high") return "/history";
  if (code.startsWith("scheduler")) return "/quality";
  if (code === "wide_spread_not_downgraded" || code === "llm_degraded_mode_events") {
    return "/history";
  }
  if (code === "direction_accuracy_low" || code === "missing_calibration_rate_high") {
    return "/history";
  }
  return null;
}

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
    <div className="flex flex-col gap-2" data-testid="anomaly-banner">
      {anomalies.map((a, i) => {
        const tone = SEVERITY_TONE[a.severity] ?? SEVERITY_TONE.low;
        const label = CODE_LABEL[a.code] ?? a.code;
        const detail =
          typeof a.detail === "string" ? a.detail : JSON.stringify(a.detail);
        const eventIds = extractEventIds(a);
        const href = a.href || defaultHref(a.code);

        return (
          <div
            key={`${a.code}-${i}`}
            className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${tone}`}
            data-testid={`anomaly-${a.code}`}
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="font-medium">
                {label}
                <span className="ml-2 rounded bg-card/50 px-1.5 py-0.5 text-xs">
                  {SEVERITY_LABEL[a.severity] ?? a.severity}
                </span>
              </span>
              <span className="text-xs opacity-80 break-all">{detail}</span>

              {(eventIds.length > 0 || href) && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                  {eventIds.slice(0, 5).map((id) => (
                    <Link
                      key={id}
                      href={`/events/${encodeURIComponent(id)}`}
                      className="inline-flex items-center gap-0.5 rounded border border-current/20 px-1.5 py-0.5 font-mono underline-offset-2 hover:underline"
                      data-testid={`anomaly-event-${id}`}
                    >
                      {id.length > 18 ? `${id.slice(0, 16)}…` : id}
                      <ExternalLink className="size-3" aria-hidden="true" />
                    </Link>
                  ))}
                  {eventIds.length > 5 && (
                    <span className="opacity-70">+{eventIds.length - 5} 更多</span>
                  )}
                  {href && (
                    <Link
                      href={href}
                      className="inline-flex items-center gap-0.5 font-medium underline-offset-2 hover:underline"
                      data-testid={`anomaly-href-${a.code}`}
                    >
                      查看相关页
                      <ExternalLink className="size-3" aria-hidden="true" />
                    </Link>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

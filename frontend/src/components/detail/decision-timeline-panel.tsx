"use client";

import { useEffect, useState } from "react";
import { History } from "lucide-react";
import { eventsApi, type DecisionTimelineResponse } from "@/lib/api";

const DRIVER_LABELS: Record<string, string> = {
  manual_resolution: "人工结算",
  llm_degraded: "LLM 降级",
  guardrail: "护栏规则触发",
  market_quality: "市场质量降级",
  source_conflict: "来源冲突",
  calibration: "证据冲突",
  market_move: "概率显著变化",
  none: "无显著变化",
  initial: "首次记录",
};

const DIRECTION_COLORS: Record<string, string> = {
  YES: "text-green-600 dark:text-green-400",
  NO: "text-red-600 dark:text-red-400",
  WAIT: "text-yellow-600 dark:text-yellow-400",
  AVOID: "text-orange-600 dark:text-orange-400",
};

export function DecisionTimelinePanel({ eventId }: { eventId: string }) {
  const [data, setData] = useState<DecisionTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      (async () => {
        if (cancelled) return;
        setLoading(true);
        setError(null);
        try {
          const result = await eventsApi.decisionTimeline(eventId);
          if (!cancelled) setData(result);
        } catch (e) {
          if (!cancelled) setError(e instanceof Error ? e.message : "决策时间线加载失败");
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [eventId]);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <History className="size-4 text-primary" aria-hidden="true" />
        <h2 className="text-sm font-semibold">决策变化时间线</h2>
      </div>
      {loading ? (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          加载中…
        </div>
      ) : data && data.snapshots.length > 0 ? (
        <div className="flex flex-col gap-2">
          {data.snapshots.map((snap, i) => {
            const diff = i > 0 ? data.diffs[i - 1] : null;
            const dirColor = snap.final_displayed_direction
              ? DIRECTION_COLORS[snap.final_displayed_direction] ?? ""
              : "";
            return (
              <div key={snap.snapshot_id}
                   className="rounded-lg border border-border bg-card px-4 py-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">
                    {new Date(snap.recorded_at).toLocaleString("zh-CN")}
                  </span>
                  <span className={`font-semibold ${dirColor}`}>
                    {snap.final_displayed_direction ?? "—"}
                  </span>
                </div>
                {diff && diff.direction_changed && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {diff.prev_direction} → {diff.current_direction}
                    <span className="ml-2 rounded bg-muted px-1.5 py-0.5">
                      {DRIVER_LABELS[diff.primary_change_driver] ?? diff.primary_change_driver}
                    </span>
                  </div>
                )}
                {snap.final_downgrade_reason && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    降级原因：{snap.final_downgrade_reason}
                  </div>
                )}
                {snap.llm_degraded_mode && (
                  <div className="mt-1 text-xs text-orange-600 dark:text-orange-400">
                    LLM 降级模式
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
          {error ?? "暂无决策时间线数据。该事件可能在 DECISION_TIMELINE_ENABLED 关闭期间保存。"}
        </div>
      )}
    </section>
  );
}

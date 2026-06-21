"use client";

import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { DecisionCard } from "@/components/decisions/decision-card";
import { eventsApi, type DecisionReport } from "@/lib/api";

export function DecisionReportPanel({ eventId }: { eventId: string }) {
  const [report, setReport] = useState<DecisionReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      (async () => {
        setLoading(true);
        setError(null);
        try {
          setReport(await eventsApi.decision(eventId));
        } catch (e) {
          setError(e instanceof Error ? e.message : "决策报告加载失败");
        } finally {
          setLoading(false);
        }
      })();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [eventId]);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Activity className="size-4 text-primary" aria-hidden="true" />
        <h2 className="text-sm font-semibold">决策分析</h2>
      </div>
      {loading ? (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          加载中…
        </div>
      ) : report ? (
        <DecisionCard report={report} />
      ) : (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
          {error ?? "暂无决策报告。该事件可能还没有冻结预测。"}
        </div>
      )}
    </section>
  );
}

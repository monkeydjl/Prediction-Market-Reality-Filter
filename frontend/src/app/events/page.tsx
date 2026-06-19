"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { SignalSummary } from "@/components/detail/signal-summary";
import { MarketPanel } from "@/components/detail/market-links";
import { SignalPanel } from "@/components/detail/signal-panel";
import { OfficialColumn, NewsColumn } from "@/components/detail/evidence-list";
import { TrackingDecision } from "@/components/detail/tracking-decision";
import { ProbabilityChart, buildSeries } from "@/components/detail/probability-chart";
import { DeltaPill, SupportMeter } from "@/components/indicators";
import { eventsApi } from "@/lib/api";
import { adaptRecord, type EventView } from "@/lib/adapt";
import { categoryLabel, fmtPct } from "@/lib/format";
import type { EventRecord, HistorySnapshot, SimilarEvent } from "@/lib/types";

function DetailInner() {
  const params = useSearchParams();
  const id = params.get("id");

  const [record, setRecord] = useState<EventRecord | null>(null);
  const [view, setView] = useState<EventView | null>(null);
  const [history, setHistory] = useState<HistorySnapshot[]>([]);
  const [similar, setSimilar] = useState<SimilarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const entry = await eventsApi.detail(id);
        if (cancelled) return;
        const rec = entry.record;
        setRecord(rec);
        setView(adaptRecord(rec));
        // History + similar are best-effort; 404 just means none yet.
        const [h, s] = await Promise.all([
          eventsApi.history(id).catch(() => ({ history: [] as HistorySnapshot[] })),
          eventsApi.similar(id).catch(() => ({ similar: [] as SimilarEvent[] })),
        ]);
        if (cancelled) return;
        setHistory(h.history ?? []);
        setSimilar(s.similar ?? []);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!id) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-sm text-muted-foreground">
        <p>缺少事件 ID</p>
        <Link href="/" className="text-primary hover:underline">
          返回监控面板
        </Link>
      </div>
    );
  }

  if (loading) {
    return <div className="grid h-40 place-items-center text-sm text-muted-foreground">加载中…</div>;
  }
  if (error || !record || !view) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-sm text-muted-foreground">
        <p>{error ?? "未找到该事件"}</p>
        <Link href="/" className="text-primary hover:underline">
          返回监控面板
        </Link>
      </div>
    );
  }

  return (
    <>
      <Link
        href="/"
        className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        返回监控面板
      </Link>

      <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded bg-secondary px-2 py-0.5 font-mono text-muted-foreground">
            {categoryLabel(view.category)}
          </span>
        </div>
        <h1 className="text-balance text-xl font-semibold md:text-2xl">
          {record.event_title_zh || view.title}
        </h1>
        {view.description && (
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">{view.description}</p>
        )}
        <div className="flex flex-wrap items-end gap-x-8 gap-y-4 pt-1">
          <div className="flex flex-col">
            <span className="text-xs text-muted-foreground">当前发生概率</span>
            <div className="flex items-center gap-2">
              <span className="font-mono text-4xl font-semibold tabular-nums">
                {fmtPct(view.currentProbability)}
              </span>
              <DeltaPill delta={view.delta} />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">基准概率</span>
            <span className="font-mono text-lg tabular-nums text-muted-foreground">
              {fmtPct(view.baselineProbability)}
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">证据支持度</span>
            <SupportMeter value={view.evidenceSupport} />
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">概率变化趋势</h2>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-0.5 w-4 bg-chart-1" aria-hidden="true" />
              模型估计
            </span>
          </div>
          <ProbabilityChart data={buildSeries(history)} baseline={view.baselineProbability} />
        </div>
        <div className="flex flex-col gap-4">
          <SignalSummary
            event={view}
            crossValidation={record.cross_validation}
            recommendedAction={record.intelligence_report?.recommended_action}
          />
          <TrackingDecision
            id={record.event_id}
            status={record.tracking?.status}
            priority={record.tracking?.priority}
          />
        </div>
      </div>

      <section className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">证据来源</h2>
          <span className="text-xs text-muted-foreground">
            官方信息 · 公开新闻 · 预测市场
          </span>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <OfficialColumn record={record} />
          <NewsColumn record={record} />
          <MarketPanel record={record} />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">证据与交叉验证</h2>
        <SignalPanel record={record} />
      </section>

      {similar.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">相似历史事件</h2>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {similar.map((s) => (
              <Link
                key={s.event_id}
                href={`/events?id=${encodeURIComponent(s.event_id)}`}
                className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40"
              >
                <p className="line-clamp-2 text-sm font-medium leading-snug">
                  {s.event_title_zh || s.event_title}
                </p>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span className="font-mono">
                    相似度 {Math.round((s.similarity ?? 0) * 100)}%
                  </span>
                  {s.estimated_probability != null && (
                    <span className="font-mono tabular-nums">{fmtPct(s.estimated_probability)}</span>
                  )}
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

export default function EventDetailPage() {
  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <Suspense fallback={<div className="grid h-40 place-items-center text-sm text-muted-foreground">加载中…</div>}>
          <DetailInner />
        </Suspense>
      </main>
    </div>
  );
}

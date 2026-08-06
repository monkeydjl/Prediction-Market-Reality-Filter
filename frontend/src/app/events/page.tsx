"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";
import { SignalSummary } from "@/components/detail/signal-summary";
import { MarketPanel } from "@/components/detail/market-links";
import { SignalPanel } from "@/components/detail/signal-panel";
import { ConfidenceBreakdownPanel } from "@/components/detail/confidence-breakdown-panel";
import { OfficialColumn, NewsColumn } from "@/components/detail/evidence-list";
import { TrackingDecision } from "@/components/detail/tracking-decision";
import { ManualResolvePanel } from "@/components/detail/manual-resolve-panel";
import { DecisionReportPanel } from "@/components/detail/decision-report-panel";
import { DecisionTimelinePanel } from "@/components/detail/decision-timeline-panel";
import { EdgeChart, ProbabilityChart, buildSeries } from "@/components/detail/probability-chart";
import { DeltaPill, SupportMeter } from "@/components/indicators";
import { eventsApi, type EdgeTrajectory } from "@/lib/api";
import { adaptEntry, type EventView } from "@/lib/adapt";
import { categoryLabel, fmtPct, fmtSignedPct } from "@/lib/format";
import type { EventRecord, HistorySnapshot, SimilarEvent, Trend } from "@/lib/types";

const TREND_PATTERN_LABELS: Record<string, string> = {
  insufficient_data: "样本不足",
  stable: "稳定",
  trending_up: "持续上行",
  trending_down: "持续下行",
  reversing: "反转中",
  volatile: "高波动",
};

const TREND_DIRECTION_LABELS: Record<string, string> = {
  rising: "上行",
  falling: "下行",
  stable: "稳定",
};

const EDGE_CLASS_LABELS: Record<string, string> = {
  fresh: "新鲜",
  decaying: "衰减中",
  stale: "已过时",
  closed: "已收敛",
  no_data: "无数据",
};

function fmtMaybePct(n: number | null | undefined, digits = 0) {
  if (n == null) return "—";
  return fmtPct(n, digits);
}

function fmtMaybeSigned(n: number | null | undefined, digits = 1) {
  if (n == null) return "—";
  return fmtSignedPct(n, digits);
}

function fmtHours(hours: number | null | undefined) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function signedTone(n: number | null | undefined) {
  if (n == null || n === 0) return "text-muted-foreground";
  return n > 0 ? "text-pos" : "text-neg";
}

function SmallMetric({ label, value, tone = "text-foreground" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className={`font-mono text-sm font-semibold tabular-nums ${tone}`}>{value}</span>
    </div>
  );
}

function TrendEdgeSummary({
  trend,
  edge,
}: {
  trend: Trend | null;
  edge: EdgeTrajectory | null;
}) {
  if (!trend && !edge) return null;
  const pattern = trend?.pattern ? TREND_PATTERN_LABELS[trend.pattern] ?? trend.pattern : "—";
  const direction = trend?.direction ? TREND_DIRECTION_LABELS[trend.direction] ?? trend.direction : "—";
  const edgeClass = edge?.classification ? EDGE_CLASS_LABELS[edge.classification] ?? edge.classification : "—";

  return (
    <div className="grid gap-4 border-t border-border pt-3 md:grid-cols-2">
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium">概率趋势</span>
          <span className="rounded bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">{pattern}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          <SmallMetric label="方向" value={direction} />
          <SmallMetric label="样本" value={String(trend?.observations ?? 0)} />
          <SmallMetric label="当前" value={fmtMaybePct(trend?.latest_probability, 1)} />
          <SmallMetric label="净变化" value={fmtMaybeSigned(trend?.net_change)} tone={signedTone(trend?.net_change)} />
          <SmallMetric label="近期变化" value={fmtMaybeSigned(trend?.recent_change)} tone={signedTone(trend?.recent_change)} />
          <SmallMetric label="平均波动" value={fmtMaybeSigned(trend?.volatility)} />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium">Edge 轨迹</span>
          <span className="rounded bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">{edgeClass}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          <SmallMetric label="样本" value={String(edge?.observations ?? 0)} />
          <SmallMetric label="年龄" value={fmtHours(edge?.age_hours)} />
          <SmallMetric label="当前 edge" value={fmtMaybeSigned(edge?.latest_edge)} tone={signedTone(edge?.latest_edge)} />
          <SmallMetric label="峰值 edge" value={fmtMaybeSigned(edge?.peak_edge)} tone={signedTone(edge?.peak_edge)} />
          <SmallMetric label="净变化" value={fmtMaybeSigned(edge?.net_edge_change)} tone={signedTone(edge?.net_edge_change)} />
          <SmallMetric label="近期变化" value={fmtMaybeSigned(edge?.recent_edge_change)} tone={signedTone(edge?.recent_edge_change)} />
        </div>
      </div>
    </div>
  );
}

function DetailInner() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get("id");

  const [record, setRecord] = useState<EventRecord | null>(null);
  const [view, setView] = useState<EventView | null>(null);
  const [history, setHistory] = useState<HistorySnapshot[]>([]);
  const [trend, setTrend] = useState<Trend | null>(null);
  const [edge, setEdge] = useState<EdgeTrajectory | null>(null);
  const [similar, setSimilar] = useState<SimilarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!id) return;
    if (!window.confirm("确定删除此事件？")) return;
    setDeleting(true);
    setError(null);
    try {
      await eventsApi.delete(id);
      router.push("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
      setDeleting(false);
    }
  };

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
        setView(adaptEntry(entry));
        // History + similar are best-effort; 404 just means none yet.
        const [h, s] = await Promise.all([
          eventsApi.history(id).catch(() => ({
            history: [] as HistorySnapshot[],
            trend: undefined,
            edge: undefined,
          })),
          eventsApi.similar(id).catch(() => ({ similar: [] as SimilarEvent[] })),
        ]);
        if (cancelled) return;
        setHistory(h.history ?? []);
        setTrend(h.trend ?? null);
        setEdge(h.edge ?? null);
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
  const series = buildSeries(history);

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
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="rounded bg-secondary px-2 py-0.5 font-mono text-muted-foreground">
            {categoryLabel(view.category)}
          </span>
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleting}
            className="inline-flex items-center gap-1.5 rounded border border-neg/40 bg-neg/10 px-2 py-1 text-xs font-medium text-neg transition-colors hover:bg-neg/20 disabled:opacity-50"
          >
            <Trash2 className="size-3" aria-hidden="true" />
            {deleting ? "删除中…" : "删除事件"}
          </button>
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
          <ProbabilityChart data={series} baseline={view.baselineProbability} />
          <TrendEdgeSummary trend={trend} edge={edge} />
          <div className="border-t border-border pt-3">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-medium">Edge 变化</h3>
              <span className="text-[11px] text-muted-foreground">AI 估计 - 市场基准</span>
            </div>
            <EdgeChart data={series} />
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <SignalSummary
            event={view}
            crossValidation={record.cross_validation}
            recommendedAction={record.intelligence_report?.recommended_action}
          />
          <TrackingDecision
            key={`${record.event_id}:${record.tracking?.status ?? ""}:${record.tracking?.priority ?? ""}`}
            id={record.event_id}
            status={record.tracking?.status}
            priority={record.tracking?.priority}
          />
          <ManualResolvePanel
            record={record}
            onResolved={(entry) => {
              setRecord(entry.record);
              setView(adaptEntry(entry));
            }}
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
        <ConfidenceBreakdownPanel record={record} />
      </section>

      <DecisionReportPanel eventId={record.event_id} />
      <DecisionTimelinePanel eventId={record.event_id} />

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
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <Suspense fallback={<div className="grid h-40 place-items-center text-sm text-muted-foreground">加载中…</div>}>
          <DetailInner />
        </Suspense>
      </main>
  );
}

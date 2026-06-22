"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Gavel,
  Loader2,
  RefreshCw,
} from "lucide-react";
import {
  eventsApi,
  type WorldCupResolveMatch,
  type WorldCupResolveResult,
} from "@/lib/api";
import type { TrackedEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

const RESOLUTION_DRY_RUN_LIMIT = 200;

function formatValue(value: unknown) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("zh-CN") : String(value);
  if (typeof value === "string") return value;
  return String(value);
}

function outcomeLabel(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${Number(value).toFixed(0)}%`;
}

function confidenceLabel(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function matchResultLabel(result: string | undefined) {
  if (result === "would_resolve") return "将结算";
  if (result === "resolved") return "已结算";
  return result || "未知";
}

function matchResultClass(result: string | undefined) {
  if (result === "would_resolve") return "border-primary/40 bg-primary/10 text-primary";
  if (result === "resolved") return "border-pos/40 bg-pos/10 text-pos";
  return "border-border bg-secondary text-muted-foreground";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function sourceRecord(detail: TrackedEntry | undefined) {
  return asRecord(detail?.record?.source);
}

function marketSummary(detail: TrackedEntry | undefined) {
  const source = sourceRecord(detail);
  const platform = text(source.platform);
  const sourceId = text(source.source_id);
  const type = text(source.type);
  const pieces = [platform, sourceId].filter(Boolean);
  if (pieces.length > 0) return pieces.join(" · ");
  return type || "—";
}

function marketUrl(detail: TrackedEntry | undefined) {
  return text(sourceRecord(detail).url);
}

function factIds(match: WorldCupResolveMatch) {
  return (match.facts ?? []).filter(Boolean).slice(0, 8);
}

function SummaryPill({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: unknown;
  tone?: "neutral" | "primary" | "warn";
}) {
  return (
    <span
      className={cn(
        "rounded-md border px-2 py-1 font-mono text-[11px]",
        tone === "primary"
          ? "border-primary/40 bg-primary/10 text-primary"
          : tone === "warn"
            ? "border-warn/40 bg-warn/10 text-warn"
            : "border-border bg-secondary text-muted-foreground",
      )}
    >
      <span className={tone === "neutral" ? "text-foreground" : ""}>{label}</span> {formatValue(value)}
    </span>
  );
}

function CandidateCard({
  match,
  detail,
  confirming,
  resolving,
  actionDisabled,
  onApprove,
}: {
  match: WorldCupResolveMatch;
  detail: TrackedEntry | undefined;
  confirming: boolean;
  resolving: boolean;
  actionDisabled: boolean;
  onApprove: (match: WorldCupResolveMatch) => void;
}) {
  const facts = factIds(match);
  const url = marketUrl(detail);
  const canApprove = Boolean(match.event_id && match.result === "would_resolve" && match.actual_outcome != null);

  return (
    <li className="grid gap-3 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-medium", matchResultClass(match.result))}>
            {matchResultLabel(match.result)}
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            confidence {confidenceLabel(match.confidence)}
          </span>
          {facts.length > 0 && (
            <span className="font-mono text-[11px] text-muted-foreground">
              facts {facts.length}
            </span>
          )}
        </div>

        <Link
          href={`/events?id=${encodeURIComponent(match.event_id ?? "")}`}
          className="mt-1 block line-clamp-2 text-sm font-medium text-foreground hover:text-primary"
        >
          {match.event_title || detail?.record.event_title_zh || detail?.record.event_title || match.event_id || "—"}
        </Link>

        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="truncate">
            <span className="text-foreground">市场</span> {marketSummary(detail)}
          </span>
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              source
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          )}
          {match.event_id && <span className="font-mono">{match.event_id}</span>}
        </div>

        {match.reason && (
          <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {match.reason}
          </p>
        )}

        {facts.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {facts.map((fact) => (
              <span key={fact} className="rounded bg-secondary px-2 py-1 font-mono text-[11px] text-muted-foreground">
                {fact}
              </span>
            ))}
          </div>
        )}

        {confirming && (
          <div className="mt-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            再次确认后写入：结果 {outcomeLabel(match.actual_outcome)}，置信度 {confidenceLabel(match.confidence)}。
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 md:flex-col md:items-end">
        <div className="font-mono text-sm font-semibold tabular-nums text-foreground md:text-right">
          {outcomeLabel(match.actual_outcome)}
        </div>
        {canApprove && (
          <button
            type="button"
            onClick={() => onApprove(match)}
            disabled={actionDisabled || resolving}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-md border px-2 text-xs font-medium transition-colors disabled:opacity-50",
              confirming
                ? "border-warn bg-warn/15 text-warn hover:bg-warn/25"
                : "border-primary bg-primary/15 text-primary hover:bg-primary/25",
            )}
          >
            {resolving && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
            {resolving ? "写入中" : confirming ? "写入结算" : "确认结算"}
          </button>
        )}
      </div>
    </li>
  );
}

export function WorldCupResolutionPanel() {
  const [result, setResult] = useState<WorldCupResolveResult | null>(null);
  const [details, setDetails] = useState<Record<string, TrackedEntry>>({});
  const [detailErrorCount, setDetailErrorCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const matches = useMemo(() => result?.matches ?? [], [result]);
  const pendingCount = result?.pending_count ?? 0;
  const noMatchCount = Math.max(0, pendingCount);

  async function load() {
    setLoading(true);
    setError(null);
    setDetailErrorCount(0);
    try {
      const dryRun = await eventsApi.worldCupResolveDryRun(RESOLUTION_DRY_RUN_LIMIT);
      const ids = Array.from(new Set((dryRun.matches ?? []).map((match) => match.event_id).filter(Boolean))) as string[];
      const settled = await Promise.allSettled(ids.map(async (id) => [id, await eventsApi.detail(id)] as const));
      const nextDetails: Record<string, TrackedEntry> = {};
      let failures = 0;
      settled.forEach((entry) => {
        if (entry.status === "fulfilled") {
          const [id, detail] = entry.value;
          nextDetails[id] = detail;
        } else {
          failures += 1;
        }
      });
      setResult(dryRun);
      setDetails(nextDetails);
      setDetailErrorCount(failures);
    } catch (e) {
      setError(e instanceof Error ? e.message : "世界杯结算 dry-run 加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function approveMatch(match: WorldCupResolveMatch) {
    const eventId = match.event_id ?? "";
    const actualOutcome = Number(match.actual_outcome);
    if (!eventId || !Number.isFinite(actualOutcome) || actualOutcome < 0 || actualOutcome > 100) {
      setError("候选结算结果无效，不能写入。");
      return;
    }
    const confidence = Number(match.confidence ?? 1);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      setError("候选结算置信度无效，不能写入。");
      return;
    }
    if (confirmingId !== eventId) {
      setConfirmingId(eventId);
      setMessage(null);
      setError(null);
      return;
    }

    setResolvingId(eventId);
    setMessage(null);
    setError(null);
    try {
      await eventsApi.resolveManual(eventId, {
        actual_outcome: actualOutcome,
        confidence,
        notes: match.reason || "World Cup structured facts",
      });
      setConfirmingId(null);
      setMessage(`已写入结算：${match.event_title || eventId} ${outcomeLabel(actualOutcome)}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "世界杯结算写入失败");
    } finally {
      setResolvingId(null);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-start">
        <div className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Gavel className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-medium">世界杯结算检查</h2>
              <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                dry-run
              </span>
              {matches.length > 0 ? (
                <span className="rounded-md border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                  {matches.length} candidates
                </span>
              ) : (
                <span className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                  no candidates
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              checked {result?.checked_count ?? "—"} · limit {RESOLUTION_DRY_RUN_LIMIT}
              {detailErrorCount > 0 ? ` · ${detailErrorCount} detail errors` : ""}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50 md:justify-self-end"
        >
          {loading ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden="true" />
          )}
          刷新检查
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs leading-relaxed text-neg">
          {error}
        </div>
      )}

      {message && (
        <div className="rounded-md border border-pos/40 bg-pos/10 px-3 py-2 text-xs leading-relaxed text-pos">
          {message}
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        <SummaryPill label="checked" value={result?.checked_count} />
        <SummaryPill label="would resolve" value={result?.resolved_count} tone="primary" />
        <SummaryPill label="pending/no match" value={noMatchCount} tone={noMatchCount > 0 ? "warn" : "neutral"} />
        <SummaryPill label="unresolved" value={result?.unresolved_events} />
      </div>

      {loading && !result ? (
        <div className="grid h-24 place-items-center rounded-md border border-border bg-secondary text-xs text-muted-foreground">
          加载 dry-run…
        </div>
      ) : !result ? (
        <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-3 text-xs text-warn">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          dry-run 未返回结果。
        </div>
      ) : matches.length === 0 ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-secondary px-3 py-3 text-xs text-muted-foreground">
          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-pos" aria-hidden="true" />
          当前没有可结算候选；待定事件仍需要更多事实或最终结果。
        </div>
      ) : (
        <ul className="divide-y divide-border border-y border-border">
          {matches.slice(0, 12).map((match, index) => (
            <CandidateCard
              key={`${match.event_id ?? "match"}:${index}`}
              match={match}
              detail={match.event_id ? details[match.event_id] : undefined}
              confirming={confirmingId === match.event_id}
              resolving={resolvingId === match.event_id}
              actionDisabled={loading || Boolean(resolvingId)}
              onApprove={(candidate) => void approveMatch(candidate)}
            />
          ))}
        </ul>
      )}

      {matches.length > 12 && (
        <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          仅显示前 12 条候选；刷新 dry-run 不会写入任何结算结果。
        </div>
      )}
    </section>
  );
}

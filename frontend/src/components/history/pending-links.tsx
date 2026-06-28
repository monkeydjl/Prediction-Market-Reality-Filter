"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Link2, Loader2 } from "lucide-react";
import { eventsApi, type PendingLink } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

function textOrDash(value: string | null | undefined) {
  const text = String(value ?? "").trim();
  return text || "—";
}

function linkKey(link: PendingLink) {
  return `${link.event_id}:${link.contract_id}`;
}

export function PendingLinks() {
  const [links, setLinks] = useState<PendingLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await eventsApi.pendingLinks();
      if (!mountedRef.current) return;
      setLinks(resp.pending ?? []);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : "待审链接加载失败");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function verify(link: PendingLink) {
    if (!link.contract_id) return;
    const key = linkKey(link);
    setVerifying(key);
    setError(null);
    try {
      await eventsApi.verifyLink(link.event_id, link.contract_id);
      if (!mountedRef.current) return;
      setLinks((items) => items.filter((item) => linkKey(item) !== key));
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : "确认关联失败");
    } finally {
      if (mountedRef.current) setVerifying(null);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link2 className="size-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">待审市场链接</h2>
          <span className="font-mono text-xs text-muted-foreground">{links.length}</span>
        </div>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
      {loading ? (
        <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
      ) : links.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">暂无待人工确认的市场链接。</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {links.map((link) => {
            const key = linkKey(link);
            return (
              <li key={key} className="flex flex-col gap-3 rounded-md border border-border bg-background/40 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded bg-secondary px-2 py-0.5 font-mono">
                      {link.market_name || "market"}
                    </span>
                    <span>置信度 {Math.round((link.link_confidence ?? 0) * 100)}%</span>
                    <span>{fmtDateTime(link.linked_at)}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/events?id=${encodeURIComponent(link.event_id)}`}
                      className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-secondary px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent"
                    >
                      查看事件
                    </Link>
                    <button
                      type="button"
                      onClick={() => verify(link)}
                      disabled={verifying === key || !link.contract_id}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
                    >
                      {verifying === key ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <Check className="size-3.5" aria-hidden="true" />
                      )}
                      确认关联
                    </button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="flex flex-col gap-2 rounded-md border border-border p-3">
                    <div className="text-[11px] font-medium text-muted-foreground">事件侧</div>
                    <p className="line-clamp-2 text-sm font-medium">
                      {textOrDash(link.event_title_zh || link.event_title || link.event_id)}
                    </p>
                    <div className="text-xs leading-relaxed text-muted-foreground">
                      <span className="font-medium text-foreground">解析标准：</span>
                      {textOrDash(link.event_resolution_criteria)}
                    </div>
                    {link.event_summary && (
                      <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                        {link.event_summary}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-col gap-2 rounded-md border border-border p-3">
                    <div className="text-[11px] font-medium text-muted-foreground">市场侧</div>
                    <p className="line-clamp-2 text-sm font-medium">
                      {textOrDash(link.market_question || link.contract_id)}
                    </p>
                    <div className="text-xs leading-relaxed text-muted-foreground">
                      <span className="font-medium text-foreground">解析标准：</span>
                      {textOrDash(link.resolution_criteria)}
                    </div>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {textOrDash(link.contract_id)}
                    </p>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

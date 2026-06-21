"use client";

import { useEffect, useState } from "react";
import { Check, Link2, Loader2 } from "lucide-react";
import { eventsApi, type PendingLink } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

export function PendingLinks() {
  const [links, setLinks] = useState<PendingLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await eventsApi.pendingLinks();
      setLinks(resp.pending ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "待审链接加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function verify(link: PendingLink) {
    if (!link.contract_id) return;
    const key = `${link.event_id}:${link.contract_id}`;
    setVerifying(key);
    setError(null);
    try {
      await eventsApi.verifyLink(link.event_id, link.contract_id);
      setLinks((items) => items.filter((item) => item !== link));
    } catch (e) {
      setError(e instanceof Error ? e.message : "确认关联失败");
    } finally {
      setVerifying(null);
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
        <ul className="divide-y divide-border">
          {links.map((link) => {
            const key = `${link.event_id}:${link.contract_id}`;
            return (
              <li key={key} className="grid gap-3 py-3 md:grid-cols-[1fr_auto] md:items-center">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {link.market_question || link.contract_id || link.event_id}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {link.market_name || "prediction market"} · 置信度{" "}
                    {Math.round((link.link_confidence ?? 0) * 100)}% · {fmtDateTime(link.linked_at)}
                  </p>
                </div>
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
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

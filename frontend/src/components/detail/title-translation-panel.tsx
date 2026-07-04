"use client";

import { useState } from "react";
import { Languages } from "lucide-react";
import { eventsApi } from "@/lib/api";
import type { EventRecord } from "@/lib/types";

export function TitleTranslationPanel({
  record,
  onTranslated,
}: {
  record: Pick<EventRecord, "event_id" | "event_title" | "event_title_zh">;
  onTranslated: (record: Partial<EventRecord>) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasTranslation = Boolean(record.event_title_zh);

  async function translate(force = false) {
    setLoading(true);
    setError(null);
    try {
      const response = await eventsApi.translateEvent(record.event_id, { force });
      onTranslated({
        ...record,
        event_title_zh: response.event_title_zh,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "标题翻译失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">标题翻译</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {hasTranslation ? record.event_title_zh : "当前事件还没有中文标题。"}
          </p>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => void translate(false)}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-wait disabled:opacity-60"
        >
          <Languages className="size-3.5" aria-hidden="true" />
          翻译标题
        </button>
      </div>
      {hasTranslation && (
        <button
          type="button"
          disabled={loading}
          onClick={() => void translate(true)}
          className="mt-3 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:cursor-wait disabled:opacity-60"
        >
          重新翻译
        </button>
      )}
      {error && <p className="mt-2 text-xs text-neg">{error}</p>}
    </section>
  );
}

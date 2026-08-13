"use client";

import { useState } from "react";
import { Languages, Loader2 } from "lucide-react";
import { eventsApi } from "@/lib/api";

export function TitleTranslationPanel({
  eventId,
  titleZh,
  onTranslated,
}: {
  eventId: string;
  titleZh?: string | null;
  onTranslated: (titleZh: string) => void;
}) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasTranslation = Boolean(titleZh);

  async function translate(force: boolean) {
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const response = await eventsApi.translateEvent(eventId, force);
      onTranslated(response.event_title_zh);
      setMessage(response.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "标题翻译失败");
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      data-testid="title-translation-panel"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="flex items-center gap-2">
        <Languages className="size-4 text-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold">标题翻译</h3>
      </div>
      <p className="text-xs text-muted-foreground">
        {hasTranslation ? titleZh : "该事件还没有中文标题，页面正在显示英文原文。"}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={pending}
          onClick={() => void translate(hasTranslation)}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 text-sm text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
        >
          {pending ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          {hasTranslation ? "重新翻译" : "翻译标题"}
        </button>
        {message && <span className="text-xs text-muted-foreground">{message}</span>}
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
    </div>
  );
}

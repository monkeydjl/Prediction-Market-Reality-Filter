"use client";

import { useState } from "react";
import { Gavel, Loader2 } from "lucide-react";
import { eventsApi } from "@/lib/api";
import type { EventRecord, TrackedEntry } from "@/lib/types";

const inputCls =
  "h-9 rounded-md border border-border bg-secondary px-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring";

export function ManualResolvePanel({
  record,
  onResolved,
}: {
  record: EventRecord;
  onResolved: (entry: TrackedEntry) => void;
}) {
  const [actual, setActual] = useState("");
  const [confidence, setConfidence] = useState("1");
  const [notes, setNotes] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (record.outcome) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 text-sm">
        <h3 className="font-semibold">事件结算</h3>
        <p className="text-muted-foreground">
          已结算：实际结果 {record.outcome.actual_outcome ?? "—"}%
        </p>
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const actualValue = Number(actual);
    if (actual.trim() === "" || !Number.isFinite(actualValue) || actualValue < 0 || actualValue > 100) {
      setError("实际结果必须在 0 到 100 之间");
      return;
    }
    const confidenceValue = Number(confidence);
    if (!Number.isFinite(confidenceValue) || confidenceValue < 0 || confidenceValue > 1) {
      setError("置信度必须在 0 到 1 之间");
      return;
    }
    if (!confirming) {
      setConfirming(true);
      setError(null);
      return;
    }
    setPending(true);
    setError(null);
    try {
      const entry = await eventsApi.resolveManual(record.event_id, {
        actual_outcome: actualValue,
        confidence: confidenceValue,
        notes: notes.trim(),
      });
      setConfirming(false);
      onResolved(entry);
    } catch (e) {
      setError(e instanceof Error ? e.message : "结算失败");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <Gavel className="size-4 text-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold">手动结算</h3>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
          实际结果（0–100）
          <input
            className={inputCls}
            type="number"
            min={0}
            max={100}
            step="any"
            value={actual}
            onChange={(e) => {
              setActual(e.target.value);
              setConfirming(false);
            }}
            placeholder="0=未发生，100=发生"
            required
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
          置信度（0–1）
          <input
            className={inputCls}
            type="number"
            min={0}
            max={1}
            step="0.01"
            value={confidence}
            onChange={(e) => {
              setConfidence(e.target.value);
              setConfirming(false);
            }}
            required
          />
        </label>
      </div>
      <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
        备注
        <textarea
          className={`${inputCls} h-20 resize-y py-2`}
          value={notes}
          onChange={(e) => {
            setNotes(e.target.value);
            setConfirming(false);
          }}
          placeholder="记录结算依据或来源"
        />
      </label>
      {confirming && (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          再次确认后写入：结果 {Number(actual)}%，置信度 {Number(confidence)}。
        </div>
      )}
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={pending}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
        >
          {pending ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          {confirming ? "写入结算" : "确认结算"}
        </button>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
    </form>
  );
}

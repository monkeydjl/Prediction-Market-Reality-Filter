"use client";

import { useState } from "react";
import {
  usePendingLinks,
  verifyLink,
  autoVerifyPending,
  type MarketLink,
  type AutoVerifyResult,
} from "@/lib/sports-api";

export function PendingReviewQueue() {
  const { data, error, isLoading, mutate } = usePendingLinks();
  const pending: MarketLink[] = data?.items ?? [];
  const [busy, setBusy] = useState(false);
  const [autoResult, setAutoResult] = useState<AutoVerifyResult | null>(null);
  const [autoError, setAutoError] = useState<string | null>(null);

  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  async function handleVerify(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, true);
    await mutate();
  }

  async function handleReject(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, false);
    await mutate();
  }

  async function handleAutoDryRun() {
    setBusy(true);
    setAutoError(null);
    try {
      const res = await autoVerifyPending({ dry_run: true });
      setAutoResult(res);
    } catch (e) {
      setAutoError(e instanceof Error ? e.message : "auto-verify failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleAutoApply() {
    setBusy(true);
    setAutoError(null);
    try {
      const res = await autoVerifyPending({ dry_run: false });
      setAutoResult(res);
      await mutate();
    } catch (e) {
      setAutoError(e instanceof Error ? e.message : "auto-verify failed");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">{errorMessage}</div>;

  return (
    <div data-testid="pending-queue" className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
        <span className="text-sm font-medium">高置信自动核验</span>
        <button
          type="button"
          data-testid="auto-verify-dry-run"
          disabled={busy}
          onClick={handleAutoDryRun}
          className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
        >
          预览候选 (dry-run)
        </button>
        <button
          type="button"
          data-testid="auto-verify-apply"
          disabled={busy}
          onClick={handleAutoApply}
          className="rounded border border-warn/50 bg-warn/10 px-2 py-1 text-xs text-warn hover:bg-warn/20 disabled:opacity-50"
        >
          执行自动核验
        </button>
        <span className="text-[11px] text-muted-foreground">
          默认阈值 0.95；需开启 PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED
        </span>
      </div>

      {autoError && (
        <div data-testid="auto-verify-error" className="text-sm text-neg">
          {autoError}
        </div>
      )}

      {autoResult && (
        <div
          data-testid="auto-verify-result"
          className="rounded border border-border bg-muted/30 p-3 text-xs font-mono"
        >
          <div>
            pending={autoResult.pending_total} · candidates={autoResult.candidates} ·
            auto_verified={autoResult.auto_verified}
            {autoResult.dry_run ? " · dry_run" : ""}
            {autoResult.enabled === false ? " · flag OFF" : ""}
          </div>
          {autoResult.message && (
            <div className="mt-1 text-muted-foreground">{autoResult.message}</div>
          )}
          {autoResult.link_ids?.length > 0 && (
            <div className="mt-1">ids: {autoResult.link_ids.join(", ")}</div>
          )}
        </div>
      )}

      {pending.length === 0 ? (
        <div data-testid="empty">无待审核链接</div>
      ) : (
        <div className="space-y-3">
          {pending.map((l) => (
            <div
              key={l.id}
              data-testid={`card-${l.id}`}
              className="rounded-lg border border-border bg-card p-3"
            >
              <p className="text-sm font-medium">{l.market_question}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {l.match_id} · {l.source} · {l.mapped_outcome}
              </p>
              <p className="mt-1 font-mono text-xs">
                confidence: {Number(l.link_confidence).toFixed(2)} ({l.link_method})
                {Number(l.link_confidence) >= 0.95 && (
                  <span className="ml-2 rounded bg-pos/15 px-1.5 py-0.5 text-[10px] text-pos">
                    ≥0.95
                  </span>
                )}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  data-testid={`confirm-${l.id}`}
                  onClick={() => handleVerify(l.match_id, l.contract_id)}
                  className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground"
                >
                  确认
                </button>
                <button
                  data-testid={`reject-${l.id}`}
                  onClick={() => handleReject(l.match_id, l.contract_id)}
                  className="rounded border border-border px-2 py-1 text-xs"
                >
                  拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

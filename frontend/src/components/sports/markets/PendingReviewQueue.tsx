"use client";
import { usePendingLinks, verifyLink, type MarketLink } from "@/lib/sports-api";

export function PendingReviewQueue() {
  const { data, error, isLoading, mutate } = usePendingLinks();
  const pending: MarketLink[] = data?.items ?? [];
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

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">{errorMessage}</div>;
  if (pending.length === 0) return <div data-testid="empty">无待审核链接</div>;

  return (
    <div data-testid="pending-queue">
      {pending.map((l) => (
        <div key={l.id} data-testid={`card-${l.id}`} className="card">
          <p>{l.market_question}</p>
          <p>
            confidence: {l.link_confidence.toFixed(2)} ({l.link_method})
          </p>
          <button
            data-testid={`confirm-${l.id}`}
            onClick={() => handleVerify(l.match_id, l.contract_id)}
          >
            确认
          </button>
          <button
            data-testid={`reject-${l.id}`}
            onClick={() => handleReject(l.match_id, l.contract_id)}
          >
            拒绝
          </button>
        </div>
      ))}
    </div>
  );
}

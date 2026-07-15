"use client";
import { useEffect, useState } from "react";
import { fetchPendingLinks, verifyLink, type MarketLink } from "@/lib/sport-markets-api";

export function PendingReviewQueue() {
  const [pending, setPending] = useState<MarketLink[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const data = await fetchPendingLinks();
    setPending(data.items);
    setLoading(false);
  }

  useEffect(() => {
    load().catch(() => setLoading(false));
  }, []);

  async function handleVerify(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, true);
    await load();
  }

  async function handleReject(matchId: string, contractId: string) {
    await verifyLink(matchId, contractId, false);
    await load();
  }

  if (loading) return <div data-testid="loading">加载中...</div>;
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

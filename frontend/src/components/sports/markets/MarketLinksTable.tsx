"use client";
import { useEffect, useState } from "react";
import { fetchMarketLinks, type MarketLink } from "@/lib/sport-markets-api";

export function MarketLinksTable({ matchId }: { matchId?: string }) {
  const [links, setLinks] = useState<MarketLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchMarketLinks(matchId ? { match_id: matchId } : {})
      .then((data) => {
        setLinks(data.items);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">{error}</div>;
  if (links.length === 0) return <div data-testid="empty">暂无市场链接</div>;

  return (
    <table data-testid="market-links-table">
      <thead>
        <tr>
          <th>Match</th>
          <th>Source</th>
          <th>Question</th>
          <th>Implied</th>
          <th>Verified</th>
        </tr>
      </thead>
      <tbody>
        {links.map((l) => (
          <tr key={l.id} data-testid={`row-${l.id}`}>
            <td>{l.match_id}</td>
            <td>{l.source}</td>
            <td>{l.market_question}</td>
            <td>{(l.implied_prob * 100).toFixed(1)}%</td>
            <td data-testid={`badge-${l.id}`}>
              {l.verified ? "已验证" : "待验证"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

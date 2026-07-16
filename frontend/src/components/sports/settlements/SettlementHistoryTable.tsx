"use client";
import { useEffect, useState } from "react";
import { fetchSettlementHistory, type MarketSettlement } from "@/lib/sport-settlements-api";

export function SettlementHistoryTable() {
  const [items, setItems] = useState<MarketSettlement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [engineFilter, setEngineFilter] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchSettlementHistory(50, engineFilter || undefined)
      .then((data) => {
        setItems(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [engineFilter]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (items.length === 0) return <div data-testid="empty">暂无结算记录</div>;

  return (
    <div>
      <div className="mb-2 flex gap-2">
        <input
          value={engineFilter}
          onChange={(e) => setEngineFilter(e.target.value)}
          placeholder="按引擎过滤"
          data-testid="engine-filter"
          className="border px-2 py-1"
        />
      </div>
      <table data-testid="settlements-table" className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b">
            <th className="text-left p-1">比赛</th>
            <th className="text-left p-1">引擎</th>
            <th className="text-left p-1">赛事</th>
            <th className="text-left p-1">结果</th>
            <th className="text-right p-1">模型概率</th>
            <th className="text-right p-1">结算概率</th>
            <th className="text-right p-1">Brier</th>
            <th className="text-center p-1">方向</th>
            <th className="text-left p-1">状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id} className="border-b">
              <td className="p-1">{s.match_id}</td>
              <td className="p-1">{s.engine}</td>
              <td className="p-1">{s.competition}</td>
              <td className="p-1">{s.mapped_outcome}</td>
              <td className="text-right p-1">
                {s.model_prob !== null ? s.model_prob.toFixed(3) : "—"}
              </td>
              <td className="text-right p-1">
                {s.settlement_implied_prob !== null ? s.settlement_implied_prob.toFixed(3) : "—"}
              </td>
              <td className="text-right p-1">
                {s.brier_score !== null ? s.brier_score.toFixed(4) : "—"}
              </td>
              <td className="text-center p-1" data-testid={`dir-${s.id}`}>
                {s.direction_correct === 1 ? "✓" : s.direction_correct === 0 ? "✗" : "—"}
              </td>
              <td className="p-1">{s.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

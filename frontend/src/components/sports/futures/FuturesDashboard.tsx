"use client";
import { useEffect, useState } from "react";
import {
  fetchAvailableFutures,
  fetchLatestSnapshots,
  type FuturesPair,
  type FuturesSnapshot,
} from "@/lib/futures-api";

export function FuturesDashboard() {
  const [pairs, setPairs] = useState<FuturesPair[] | null>(null);
  const [selected, setSelected] = useState<FuturesPair | null>(null);
  const [snapshots, setSnapshots] = useState<FuturesSnapshot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load available (competition, season) pairs on mount
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchAvailableFutures()
      .then((data) => {
        setPairs(data.pairs);
        setLoading(false);
        // Auto-select first pair if available
        if (data.pairs.length > 0) {
          setSelected(data.pairs[0]);
        }
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  // Load snapshots when a pair is selected
  useEffect(() => {
    if (!selected) return;
    setSnapshots(null);
    setError(null);
    fetchLatestSnapshots(selected.competition, selected.season)
      .then((data) => {
        setSnapshots(data.snapshots);
      })
      .catch((err) => {
        setError(err.message);
      });
  }, [selected]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!pairs || pairs.length === 0)
    return <div data-testid="empty">暂无期货市场数据</div>;

  return (
    <div className="space-y-6">
      <div className="flex gap-2">
        {pairs.map((p) => (
          <button
            key={`${p.competition}-${p.season}`}
            onClick={() => setSelected(p)}
            className={`px-3 py-1 rounded border ${
              selected?.competition === p.competition && selected?.season === p.season
                ? "bg-blue-600 text-white"
                : "bg-white text-black"
            }`}
          >
            {p.competition} {p.season}
          </button>
        ))}
      </div>

      {snapshots === null ? (
        <div>加载快照中...</div>
      ) : snapshots.length === 0 ? (
        <div>该赛事暂无快照数据</div>
      ) : (
        <div data-testid="snapshots-table" className="space-y-4">
          <h2 className="text-xl font-bold">
            {selected?.competition} {selected?.season} 最新价格
          </h2>
          <table className="w-full border-collapse border">
            <thead>
              <tr className="bg-gray-100">
                <th className="border p-2 text-left">Team</th>
                <th className="border p-2 text-left">Implied Prob</th>
                <th className="border p-2 text-left">Price</th>
                <th className="border p-2 text-left">Liquidity</th>
                <th className="border p-2 text-left">Volume</th>
                <th className="border p-2 text-left">Captured At</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((s) => (
                <tr key={s.id}>
                  <td className="border p-2">{s.team ?? "-"}</td>
                  <td className="border p-2">{s.implied_prob.toFixed(4)}</td>
                  <td className="border p-2">{s.price !== null ? s.price.toFixed(4) : "-"}</td>
                  <td className="border p-2">{s.liquidity ?? "-"}</td>
                  <td className="border p-2">{s.volume ?? "-"}</td>
                  <td className="border p-2">{s.captured_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

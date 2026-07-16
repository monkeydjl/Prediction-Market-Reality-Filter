"use client";
import { useEffect, useState } from "react";
import {
  useAvailableFutures,
  useLatestSnapshots,
} from "@/lib/sports-api";
import type { FuturesPair } from "@/lib/sports-api";

export function FuturesDashboard() {
  const {
    data: futuresData,
    error: pairsError,
    isLoading: pairsLoading,
  } = useAvailableFutures();
  const pairs = futuresData?.pairs ?? null;

  const [selected, setSelected] = useState<FuturesPair | null>(null);

  // Auto-select the first pair once the list loads (mirrors the original
  // behavior in the mount useEffect).
  useEffect(() => {
    if (pairs && pairs.length > 0 && !selected) {
      setSelected(pairs[0]);
    }
  }, [pairs, selected]);

  const {
    data: snapshotsData,
    error: snapshotsError,
    isLoading: snapshotsLoading,
  } = useLatestSnapshots(
    selected?.competition ?? null,
    selected?.season ?? null,
  );
  const snapshots = snapshotsData?.snapshots ?? null;

  const error = pairsError ?? snapshotsError;
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (pairsLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">错误: {errorMessage}</div>;
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

      {snapshotsLoading && snapshots === null ? (
        <div>加载快照中...</div>
      ) : snapshots === null ? (
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

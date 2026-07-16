"use client";
import { useEffect, useState } from "react";
import {
  fetchOptimizationParams,
  type OptimizedParams,
} from "@/lib/optimization-api";

export function OptimizationDashboard() {
  const [params, setParams] = useState<OptimizedParams[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchOptimizationParams()
      .then((data) => {
        setParams(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!params || params.length === 0)
    return <div data-testid="empty">暂无优化参数</div>;

  return (
    <div data-testid="params-table" className="space-y-4">
      <h2 className="text-xl font-bold">参数优化结果</h2>
      <table className="w-full border-collapse border">
        <thead>
          <tr className="bg-gray-100">
            <th className="border p-2 text-left">Sport</th>
            <th className="border p-2 text-left">Score</th>
            <th className="border p-2 text-left">Accuracy</th>
            <th className="border p-2 text-left">Brier</th>
            <th className="border p-2 text-left">MAE</th>
            <th className="border p-2 text-left">Samples</th>
            <th className="border p-2 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {params.map((p) => (
            <tr key={p.id}>
              <td className="border p-2">{p.sport}</td>
              <td className="border p-2">{p.score.toFixed(4)}</td>
              <td className="border p-2">{p.accuracy.toFixed(4)}</td>
              <td className="border p-2">{p.brier_score.toFixed(4)}</td>
              <td className="border p-2">{p.mae.toFixed(4)}</td>
              <td className="border p-2">{p.sample_count}</td>
              <td className="border p-2">{p.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

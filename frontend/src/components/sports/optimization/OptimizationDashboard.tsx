"use client";
import { useOptimizationParams, type OptimizedParams } from "@/lib/sports-api";

export function OptimizationDashboard() {
  const { data: params, error, isLoading } = useOptimizationParams();
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">错误: {errorMessage}</div>;
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

"use client";
import { useEffect, useState } from "react";
import { fetchCalibrations, type MarketCalibration } from "@/lib/sport-settlements-api";

export function MarketCalibrationPanel() {
  const [items, setItems] = useState<MarketCalibration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchCalibrations()
      .then((data) => {
        setItems(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (items.length === 0) return <div data-testid="empty">暂无市场校准数据</div>;

  return (
    <div data-testid="calibration-panel" className="grid gap-2">
      {items.map((cal) => {
        const isWellCalibrated = Math.abs(cal.slope - 1.0) < 0.2;
        return (
          <div
            key={cal.id}
            data-testid={`cal-card-${cal.id}`}
            className={`border p-3 rounded ${isWellCalibrated ? "border-green-500" : "border-yellow-500"}`}
          >
            <div className="flex justify-between">
              <span className="font-mono text-sm">{cal.engine}</span>
              <span className="text-xs text-muted-foreground">{cal.competition}</span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 text-xs">
              <span>斜率: {cal.slope.toFixed(3)}</span>
              <span>截距: {cal.intercept.toFixed(3)}</span>
              <span>样本数: {cal.sample_count}</span>
              <span>方向准确率: {(cal.direction_accuracy * 100).toFixed(1)}%</span>
              <span>平均 Brier: {cal.avg_brier.toFixed(4)}</span>
              <span>平均误差: {cal.avg_signed_error.toFixed(4)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

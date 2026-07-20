"use client";
import { useCalibrations, type MarketCalibration } from "@/lib/sports-api";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

export function MarketCalibrationPanel() {
  const { data, error, isLoading } = useCalibrations();
  const items: MarketCalibration[] = data?.items ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;
  const disabled = isServiceUnavailable(error);

  if (isLoading) return <div data-testid="loading">加载中...</div>;

  if (disabled) {
    return (
      <FeatureDisabledBanner
        flag="PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED=true"
        title="市场校准未启用"
        testId="calibrations-disabled"
      />
    );
  }

  if (errorMessage) return <div data-testid="error">错误: {errorMessage}</div>;
  if (items.length === 0) {
    return (
      <div data-testid="empty" className="text-sm text-muted-foreground">
        暂无市场校准数据。结算样本积累后会在此显示斜率/Brier。
      </div>
    );
  }

  return (
    <div data-testid="calibration-panel" className="grid gap-2">
      {items.map((cal) => {
        const isWellCalibrated = Math.abs(cal.slope - 1.0) < 0.2;
        return (
          <div
            key={cal.id}
            data-testid={`cal-card-${cal.id}`}
            className={`rounded border p-3 ${
              isWellCalibrated ? "border-green-500" : "border-yellow-500"
            }`}
          >
            <div className="flex justify-between">
              <span className="font-mono text-sm">{cal.engine}</span>
              <span className="text-xs text-muted-foreground">
                {cal.competition}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 text-xs">
              <span>斜率: {cal.slope.toFixed(3)}</span>
              <span>截距: {cal.intercept.toFixed(3)}</span>
              <span>样本数: {cal.sample_count}</span>
              <span>
                方向准确率: {(cal.direction_accuracy * 100).toFixed(1)}%
              </span>
              <span>平均 Brier: {cal.avg_brier.toFixed(4)}</span>
              <span>平均误差: {cal.avg_signed_error.toFixed(4)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

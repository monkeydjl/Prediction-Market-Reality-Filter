import { memo } from "react";

import type { Trend } from "@/lib/adapt";

type SparklineProps = {
  data: number[];
  trend: Trend;
  label?: string;
  width?: number;
  height?: number;
};

// Hand-rolled SVG sparkline — no chart lib, works in static export.
function SparklineBase({
  data,
  trend,
  label = "概率趋势",
  width = 88,
  height = 28,
}: SparklineProps) {
  if (!data || data.length < 2) {
    return <div className="h-7 w-[88px]" role="img" aria-label={`${label}暂无足够数据`} />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const pad = 3;

  const points = data.map((v, i) => {
    const x = i * stepX;
    const y = pad + (height - pad * 2) * (1 - (v - min) / range);
    return [x, y] as const;
  });

  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

  const color =
    trend === "up"
      ? "var(--color-pos)"
      : trend === "down"
        ? "var(--color-neg)"
        : "var(--color-muted-foreground)";
  const desc =
    trend === "up"
      ? "整体上行"
      : trend === "down"
        ? "整体下行"
        : "整体平稳";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      role="img"
      aria-label={`${label}：${desc}，从 ${data[0].toFixed(0)}% 到 ${data[data.length - 1].toFixed(0)}%`}
    >
      <title>{label}</title>
      <desc>{desc}</desc>
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={color} opacity="0.1" />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={points[points.length - 1][0]}
        cy={points[points.length - 1][1]}
        r="2"
        fill={color}
      />
    </svg>
  );
}

export const Sparkline = memo(SparklineBase);
Sparkline.displayName = "Sparkline";

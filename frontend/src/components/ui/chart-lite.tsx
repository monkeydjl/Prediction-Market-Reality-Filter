"use client";

import * as React from "react";
import { ResponsiveContainer, Tooltip } from "recharts";

// Minimal recharts wrapper to replace shadcn's ChartContainer/ChartTooltip
// (avoids pulling in shadcn + its deps). Tokens come from globals.css.

export function ChartFrame({
  height = 280,
  children,
}: {
  height?: number;
  children: React.ReactElement;
}) {
  return (
    <div style={{ height }} className="w-full text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground">
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

type TipRow = { name?: string; value?: number | string; color?: string };

export function DarkTooltip({
  unit = "",
  formatter,
}: {
  unit?: string;
  formatter?: (value: number | string, name: string, payload: Record<string, unknown>) => React.ReactNode;
}) {
  return (
    <Tooltip
      cursor={{ stroke: "var(--border)", fill: "var(--secondary)" }}
      contentStyle={{
        background: "var(--popover)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        fontSize: 12,
        color: "var(--foreground)",
      }}
      labelStyle={{ color: "var(--muted-foreground)", marginBottom: 4 }}
      formatter={(value, name, item) => {
        if (formatter) {
          return formatter(
            value as number | string,
            String(name),
            (item?.payload ?? {}) as Record<string, unknown>,
          );
        }
        const row = item as unknown as TipRow;
        return [`${value}${unit}`, row?.name ?? String(name)];
      }}
    />
  );
}

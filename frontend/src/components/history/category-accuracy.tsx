"use client";

import dynamic from "next/dynamic";
import { categoryLabel } from "@/lib/format";
import type { CalibrationAgg } from "@/lib/api";

type CategoryCalibration = CalibrationAgg & {
  segment_min_samples?: number | null;
  qualified?: boolean;
};

export interface CategoryDatum {
  category: string;
  brier: number | null;
  skill: number;
  count: number;
  minSamples: number | null;
  qualified: boolean | null;
}

export function toCategoryData(
  byCat: Record<string, CategoryCalibration>,
  defaultMinSamples: number | null = null,
): CategoryDatum[] {
  return Object.entries(byCat)
    .filter(([, v]) => v.skill_score != null && v.n > 0)
    .map(([category, v]) => ({
      category: categoryLabel(category),
      brier: v.brier_score == null ? null : Number(v.brier_score),
      skill: Number(v.skill_score),
      count: v.n,
      minSamples: v.segment_min_samples ?? defaultMinSamples,
      qualified: v.qualified ?? (v.segment_min_samples != null ? v.n >= v.segment_min_samples : null),
    }))
    .sort((a, b) => b.skill - a.skill);
}

function ChartLoading() {
  return (
    <div
      className="h-[260px] w-full animate-pulse rounded-md border border-border bg-secondary/50"
      aria-hidden="true"
    />
  );
}

const CategoryAccuracyChart = dynamic(
  () => import("./category-accuracy-chart").then((mod) => mod.CategoryAccuracyChart),
  {
    ssr: false,
    loading: () => <ChartLoading />,
  },
);

export function CategoryAccuracy({ data }: { data: CategoryDatum[] }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">各领域 skill 得分</h2>
        <span className="text-xs text-muted-foreground">越高越可信，负值表示跑输基线</span>
      </div>
      {data.length === 0 ? (
        <div className="grid h-[260px] place-items-center text-sm text-muted-foreground">
          暂无已结算事件，校准数据将在事件结算后生成。
        </div>
      ) : (
        <CategoryAccuracyChart data={data} />
      )}
      {data.length > 0 && (
        <div className="grid gap-2 border-t border-border pt-3 md:grid-cols-2">
          {data.map((d) => {
            const progress = d.minSamples ? Math.min(100, Math.round((d.count / d.minSamples) * 100)) : null;
            return (
              <div key={d.category} className="grid gap-1.5">
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="truncate text-foreground">{d.category}</span>
                  <span className={d.qualified ? "font-mono text-pos" : "font-mono text-muted-foreground"}>
                    {d.minSamples ? `${d.count}/${d.minSamples}` : d.count}
                    {d.qualified != null ? (d.qualified ? " 已合格" : " 未合格") : ""}
                  </span>
                </div>
                {progress != null && (
                  <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
                    <div
                      className={d.qualified ? "h-full bg-pos" : "h-full bg-warn"}
                      style={{ width: `${progress}%` }}
                      aria-hidden="true"
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useOptimizationParams } from "@/lib/sports-api";
import type { OptimizedParams } from "@/lib/sports-api/types";
import {
  buildWeightDiff,
  parseWeightMap,
} from "@/lib/sports-api/param-weights";
import { WeightDiffTable } from "@/components/sports/optimization/WeightDiffTable";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

function AppliedCard({ p }: { p: OptimizedParams }) {
  const after = useMemo(() => parseWeightMap(p.factor_weights), [p.factor_weights]);
  const elo = useMemo(() => parseWeightMap(p.elo_params), [p.elo_params]);
  // On learning page we only know currently applied set — show as after-only (before empty)
  const rows = useMemo(() => buildWeightDiff({}, after), [after]);
  const eloRows = useMemo(() => buildWeightDiff({}, elo), [elo]);

  return (
    <div
      className="rounded border border-border p-4 space-y-3"
      data-testid={`applied-card-${p.sport}-${p.competition}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="font-medium">
            {p.sport} · {p.competition}
          </span>
          <span className="ml-2 text-xs text-muted-foreground">
            id={p.id}
            {p.applied_at ? ` · 应用 ${p.applied_at}` : ""}
          </span>
        </div>
        <div className="text-xs text-muted-foreground font-mono">
          score {p.score?.toFixed?.(4) ?? p.score} · Brier{" "}
          {p.brier_score?.toFixed?.(4) ?? p.brier_score} · n={p.sample_count}
        </div>
      </div>
      <WeightDiffTable
        rows={rows}
        title="当前已应用因子权重"
        emptyText="该参数集无 factor_weights"
        testId={`applied-weights-${p.id}`}
      />
      {eloRows.length > 0 && (
        <WeightDiffTable
          rows={eloRows}
          title="Elo 参数"
          testId={`applied-elo-${p.id}`}
        />
      )}
    </div>
  );
}

export function AppliedWeightsPanel() {
  const { data, error, isLoading, mutate } = useOptimizationParams();
  const [sportFilter, setSportFilter] = useState<string>("all");

  const applied = useMemo(() => {
    const list = (data ?? []).filter((p) => p.status === "applied");
    if (sportFilter === "all") return list;
    return list.filter((p) => p.sport === sportFilter);
  }, [data, sportFilter]);

  const sports = useMemo(() => {
    const s = new Set((data ?? []).map((p) => p.sport));
    return Array.from(s).sort();
  }, [data]);

  if (isLoading) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="applied-weights-loading">
        加载已应用参数…
      </p>
    );
  }

  if (error && isServiceUnavailable(error)) {
    return (
      <FeatureDisabledBanner
        flag="PHASE9_ACCURACY_SPRINT_ENABLED=true"
        title="Phase 9 参数优化未启用"
        testId="applied-weights-disabled"
      />
    );
  }

  if (error) {
    return (
      <p className="text-sm text-neg" data-testid="applied-weights-error">
        {error instanceof Error ? error.message : "加载失败"}
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="applied-weights-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">已应用优化权重</h2>
          <p className="text-xs text-muted-foreground">
            来自 Phase9「参数优化」apply 后的当前生效集。Apply 前后对比在优化页点「应用」后可见；
            完整操作请到{" "}
            <Link href="/sports/optimization" className="text-primary underline">
              参数优化
            </Link>
            。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">
            运动
            <select
              data-testid="applied-sport-filter"
              className="ml-1 rounded border px-2 py-1 text-sm"
              value={sportFilter}
              onChange={(e) => setSportFilter(e.target.value)}
            >
              <option value="all">全部</option>
              {sports.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="rounded border px-2 py-1 text-xs hover:bg-muted"
            onClick={() => void mutate()}
          >
            刷新
          </button>
        </div>
      </div>

      {applied.length === 0 ? (
        <div
          className="rounded border border-dashed border-border p-6 text-center text-sm text-muted-foreground"
          data-testid="applied-weights-empty"
        >
          尚无 status=applied 的参数集。请先在「参数优化」导入赛季、运行优化并应用。
        </div>
      ) : (
        <div className="space-y-3">
          {applied.map((p) => (
            <AppliedCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}

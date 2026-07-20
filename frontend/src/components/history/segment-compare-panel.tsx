"use client";

import { useMemo, useState } from "react";
import { categoryLabel } from "@/lib/format";
import type {
  CalibrationBucketSummary,
  PredictionCalibration,
} from "@/lib/api";
import type { CategoryDatum } from "./category-accuracy";

type ViewMode = "segment" | "act_category" | "edge_bucket" | "confidence_bucket";
type SortKey = "label" | "skill" | "brier" | "n" | "hit";

interface CompareRow {
  key: string;
  label: string;
  n: number;
  brier: number | null;
  skill: number | null;
  hit: number | null;
  qualified: boolean | null;
  minSamples: number | null;
  grade?: string;
  marketBrier?: number | null;
}

const VIEW_LABELS: Record<ViewMode, string> = {
  segment: "领域技能（全决策）",
  act_category: "行动类目（仅 act）",
  edge_bucket: "Edge 分桶",
  confidence_bucket: "置信度分桶",
};

function fmt(n: number | null | undefined, digits = 3): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

function rowsFromSegments(
  data: CategoryDatum[],
  predCal: PredictionCalibration | null,
): CompareRow[] {
  const segs = predCal?.segments ?? {};
  return data.map((d) => {
    // CategoryDatum.category is already labelized; recover raw key if possible
    const rawEntry = Object.entries(segs).find(
      ([k]) => categoryLabel(k) === d.category,
    );
    const raw = rawEntry?.[1];
    return {
      key: d.category,
      label: d.category,
      n: d.count,
      brier: d.brier,
      skill: d.skill,
      hit: null,
      qualified: d.qualified,
      minSamples: d.minSamples,
      marketBrier: raw?.market_brier_score ?? null,
    };
  });
}

function rowsFromActCategory(predCal: PredictionCalibration | null): CompareRow[] {
  const by = predCal?.by_category ?? {};
  return Object.entries(by)
    .filter(([, v]) => (v?.n ?? 0) > 0)
    .map(([cat, v]) => ({
      key: cat,
      label: categoryLabel(cat),
      n: v.n,
      brier: v.brier_score ?? null,
      skill: v.skill_score ?? null,
      hit: null,
      qualified: null,
      minSamples: predCal?.segment_min_samples ?? null,
      grade: v.grade,
    }))
    .sort((a, b) => (b.skill ?? -999) - (a.skill ?? -999));
}

function rowsFromBuckets(
  buckets: Record<string, { n: number; brier_score: number | null; direction_correct_rate: number | null }>,
): CompareRow[] {
  return Object.entries(buckets)
    .filter(([, v]) => (v?.n ?? 0) > 0)
    .map(([k, v]) => ({
      key: k,
      label: k,
      n: v.n,
      brier: v.brier_score,
      skill: null,
      hit: v.direction_correct_rate,
      qualified: null,
      minSamples: null,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function sortRows(rows: CompareRow[], key: SortKey, dir: "asc" | "desc"): CompareRow[] {
  const mul = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (key === "label") return mul * a.label.localeCompare(b.label);
    const av = key === "skill" ? a.skill : key === "brier" ? a.brier : key === "hit" ? a.hit : a.n;
    const bv = key === "skill" ? b.skill : key === "brier" ? b.brier : key === "hit" ? b.hit : b.n;
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return mul * (av - bv);
  });
}

export interface SegmentComparePanelProps {
  categoryData: CategoryDatum[];
  predCal: PredictionCalibration | null;
  buckets: CalibrationBucketSummary | null;
  bucketsError?: string | null;
  bucketsLoading?: boolean;
}

export function SegmentComparePanel({
  categoryData,
  predCal,
  buckets,
  bucketsError,
  bucketsLoading,
}: SegmentComparePanelProps) {
  const [mode, setMode] = useState<ViewMode>("segment");
  const [sortKey, setSortKey] = useState<SortKey>("skill");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const baseRows = useMemo(() => {
    if (mode === "segment") return rowsFromSegments(categoryData, predCal);
    if (mode === "act_category") return rowsFromActCategory(predCal);
    if (mode === "edge_bucket") {
      return rowsFromBuckets(buckets?.by_edge_bucket ?? {});
    }
    return rowsFromBuckets(buckets?.by_confidence_bucket ?? {});
  }, [mode, categoryData, predCal, buckets]);

  const rows = useMemo(
    () => sortRows(baseRows, sortKey, sortDir),
    [baseRows, sortKey, sortDir],
  );

  const showSkill = mode === "segment" || mode === "act_category";
  const showHit = mode === "edge_bucket" || mode === "confidence_bucket";
  const showMarket = mode === "segment";

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "label" || key === "brier" ? "asc" : "desc");
    }
  };

  const sortMark = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const bestSkill = showSkill
    ? rows.reduce<number | null>((acc, r) => {
        if (r.skill == null) return acc;
        return acc == null || r.skill > acc ? r.skill : acc;
      }, null)
    : null;
  const worstBrier = rows.reduce<number | null>((acc, r) => {
    if (r.brier == null) return acc;
    return acc == null || r.brier > acc ? r.brier : acc;
  }, null);

  return (
    <div
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5"
      data-testid="segment-compare-panel"
    >
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold">分面对比（类别 / 分桶）</h2>
        <p className="text-xs text-muted-foreground">
          对比事件情报在不同领域、行动类目与 Edge/置信度分桶上的表现。
          体育 Kernel 引擎对比请用「学习 → 性能对比」。
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {(Object.keys(VIEW_LABELS) as ViewMode[]).map((m) => (
          <button
            key={m}
            type="button"
            data-testid={`compare-mode-${m}`}
            onClick={() => {
              setMode(m);
              setSortKey(m === "segment" || m === "act_category" ? "skill" : "n");
              setSortDir("desc");
            }}
            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
              mode === m
                ? "bg-secondary font-medium text-foreground"
                : "text-muted-foreground hover:bg-secondary/60"
            }`}
          >
            {VIEW_LABELS[m]}
          </button>
        ))}
      </div>

      {(mode === "edge_bucket" || mode === "confidence_bucket") && bucketsLoading && (
        <p className="text-sm text-muted-foreground">加载分桶…</p>
      )}
      {(mode === "edge_bucket" || mode === "confidence_bucket") && bucketsError && (
        <p className="text-sm text-neg" data-testid="buckets-error">
          {bucketsError}
        </p>
      )}

      {rows.length === 0 ? (
        <div
          className="grid h-32 place-items-center text-sm text-muted-foreground"
          data-testid="compare-empty"
        >
          {mode === "segment"
            ? "暂无领域分段数据。"
            : mode === "act_category"
              ? "暂无已行动并结算的类目样本。"
              : "暂无 Edge/置信度分桶（需 Phase3 快照字段）。"}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm" data-testid="compare-table">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="p-2">
                  <button type="button" onClick={() => toggleSort("label")}>
                    名称{sortMark("label")}
                  </button>
                </th>
                <th className="p-2 text-right">
                  <button type="button" onClick={() => toggleSort("n")}>
                    样本{sortMark("n")}
                  </button>
                </th>
                <th className="p-2 text-right">
                  <button type="button" onClick={() => toggleSort("brier")}>
                    Brier{sortMark("brier")}
                  </button>
                </th>
                {showSkill && (
                  <th className="p-2 text-right">
                    <button type="button" onClick={() => toggleSort("skill")}>
                      Skill{sortMark("skill")}
                    </button>
                  </th>
                )}
                {showMarket && (
                  <th className="p-2 text-right">市场 Brier</th>
                )}
                {showHit && (
                  <th className="p-2 text-right">
                    <button type="button" onClick={() => toggleSort("hit")}>
                      方向命中{sortMark("hit")}
                    </button>
                  </th>
                )}
                <th className="p-2 text-right">状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isBest =
                  showSkill && bestSkill != null && r.skill === bestSkill;
                const isWorst =
                  worstBrier != null && r.brier === worstBrier && rows.length > 1;
                return (
                  <tr
                    key={r.key}
                    className={`border-b border-border/60 ${
                      isBest ? "bg-pos/5" : isWorst ? "bg-neg/5" : ""
                    }`}
                    data-testid={`compare-row-${r.key}`}
                  >
                    <td className="p-2 font-medium">{r.label}</td>
                    <td className="p-2 text-right font-mono tabular-nums">{r.n}</td>
                    <td className="p-2 text-right font-mono tabular-nums">
                      {fmt(r.brier)}
                    </td>
                    {showSkill && (
                      <td
                        className={`p-2 text-right font-mono tabular-nums ${
                          (r.skill ?? 0) > 0
                            ? "text-pos"
                            : (r.skill ?? 0) < 0
                              ? "text-neg"
                              : ""
                        }`}
                      >
                        {fmt(r.skill, 2)}
                      </td>
                    )}
                    {showMarket && (
                      <td className="p-2 text-right font-mono tabular-nums text-muted-foreground">
                        {fmt(r.marketBrier)}
                      </td>
                    )}
                    {showHit && (
                      <td className="p-2 text-right font-mono tabular-nums">
                        {fmtPct(r.hit)}
                      </td>
                    )}
                    <td className="p-2 text-right text-xs text-muted-foreground">
                      {r.qualified === true
                        ? "已合格"
                        : r.qualified === false
                          ? r.minSamples
                            ? `${r.n}/${r.minSamples} 未合格`
                            : "未合格"
                          : r.grade ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {mode === "edge_bucket" &&
        buckets &&
        Object.keys(buckets.by_edge_x_confidence || {}).length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium text-foreground">
              Edge × 置信度交叉表（{Object.keys(buckets.by_edge_x_confidence).length} 格）
            </summary>
            <ul className="mt-2 grid gap-1 sm:grid-cols-2" data-testid="cross-cells">
              {Object.entries(buckets.by_edge_x_confidence)
                .filter(([, c]) => c.n > 0)
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([k, c]) => (
                  <li key={k} className="font-mono">
                    {k}: n={c.n} Brier={fmt(c.brier_score)} 命中=
                    {fmtPct(c.direction_correct_rate)}
                  </li>
                ))}
            </ul>
          </details>
        )}
    </div>
  );
}

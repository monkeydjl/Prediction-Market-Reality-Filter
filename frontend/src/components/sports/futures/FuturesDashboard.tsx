"use client";
import { useState } from "react";
import {
  useAvailableFutures,
  useFuturesCoverage,
  useLatestSnapshots,
} from "@/lib/sports-api";
import type { FuturesMultiLegIntegrity, FuturesPair } from "@/lib/sports-api";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import { FuturesLegsTable } from "./futures-legs-table";
import { FuturesSeriesPanel } from "./futures-series-panel";

function integrityBadgeClass(status: string | undefined): string {
  switch (status) {
    case "ok":
      return "bg-pos/15 text-pos";
    case "thin":
      return "bg-warn/15 text-warn";
    case "warn":
      return "bg-warn/15 text-warn";
    case "incomplete":
      return "bg-neg/15 text-neg";
    default:
      return "bg-muted text-muted-foreground";
  }
}

function IntegritySummary({
  integrity,
  testId,
}: {
  integrity?: FuturesMultiLegIntegrity | null;
  testId?: string;
}) {
  if (!integrity) return null;
  const sum =
    integrity.sum_implied_prob != null
      ? integrity.sum_implied_prob.toFixed(3)
      : "—";
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded px-1.5 py-0.5 font-medium ${integrityBadgeClass(integrity.status)}`}
        >
          多腿 {integrity.status}
        </span>
        <span>
          腿数 {integrity.leg_count} · 球队 {integrity.unique_team_count}
        </span>
        <span className="font-mono">Σp={sum}</span>
        {integrity.issues?.length > 0 && (
          <span className="text-warn">issues: {integrity.issues.join(", ")}</span>
        )}
      </div>
    </div>
  );
}

/**
 * @param competition - Deep-linked pair key from `?competition=` (OQ-3). When it
 *   matches an available pair it wins over local clicks, so the URL stays the
 *   single source of truth for which pair is on screen.
 */
export function FuturesDashboard({
  competition,
  season,
}: {
  competition?: string | null;
  season?: string | null;
} = {}) {
  const {
    data: futuresData,
    error: pairsError,
    isLoading: pairsLoading,
  } = useAvailableFutures();
  const pairs = futuresData?.pairs ?? null;

  const {
    data: coverage,
    error: coverageError,
    isLoading: coverageLoading,
  } = useFuturesCoverage();

  const [picked, setPicked] = useState<FuturesPair | null>(null);

  // Precedence: URL query param → local click → first pair. Derived at render
  // rather than assigned from an effect, which would setState synchronously on
  // mount.
  const fromQuery =
    competition && season
      ? pairs?.find((p) => p.competition === competition && p.season === season) ?? null
      : null;
  const selected = fromQuery ?? picked ?? pairs?.[0] ?? null;

  const {
    data: snapshotsData,
    error: snapshotsError,
    isLoading: snapshotsLoading,
  } = useLatestSnapshots(
    selected?.competition ?? null,
    selected?.season ?? null,
  );
  const snapshots = snapshotsData?.snapshots ?? null;
  const selectedIntegrity =
    snapshotsData?.integrity ?? selected?.integrity ?? null;

  const error = pairsError ?? snapshotsError;
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (pairsLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">错误: {errorMessage}</div>;
  if (!pairs || pairs.length === 0)
    return (
      <div className="space-y-4">
        <div data-testid="empty">暂无期货市场数据</div>
        {!coverageLoading && coverage && (
          <CoveragePanel coverage={coverage} error={coverageError} />
        )}
        <FuturesSeriesPanel pairs={[]} />
      </div>
    );

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Kalshi 多腿冠军/系列市场（Phase 12）。覆盖注册表与多腿完整性（腿数、Σ 隐含概率、重复球队）如下。
        需开启{" "}
        <code className="rounded bg-muted px-1">PHASE12_FUTURES_MARKETS_ENABLED</code>。
      </p>

      {!coverageLoading && coverage && (
        <CoveragePanel coverage={coverage} error={coverageError} />
      )}

      <FuturesSeriesPanel pairs={pairs} />

      <div className="flex flex-wrap gap-2">
        {pairs.map((p) => {
          const st = p.integrity?.status;
          const isSelected =
            selected?.competition === p.competition && selected?.season === p.season;
          return (
            <button
              key={`${p.competition}-${p.season}`}
              type="button"
              aria-pressed={isSelected}
              onClick={() => setPicked(p)}
              className={`rounded border px-3 py-1 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                isSelected
                  ? "border-primary bg-primary/15 text-primary"
                  : "border-border bg-card text-foreground hover:bg-muted"
              }`}
            >
              {p.competition} {p.season}
              {p.verified_count != null && (
                <span className="ml-1 tabular-nums opacity-80">({p.verified_count})</span>
              )}
              {st && st !== "ok" && (
                <span className="ml-1 text-[10px] uppercase opacity-90">{st}</span>
              )}
            </button>
          );
        })}
      </div>

      <IntegritySummary
        integrity={selectedIntegrity}
        testId="selected-pair-integrity"
      />

      {snapshotsLoading && snapshots === null ? (
        <div>加载快照中...</div>
      ) : snapshots === null ? (
        <div>加载快照中...</div>
      ) : snapshots.length === 0 ? (
        <div>该赛事暂无快照数据</div>
      ) : (
        <div data-testid="snapshots-table" className="space-y-4">
          <h2 className="text-base font-semibold">
            {selected?.competition} {selected?.season} 最新价格
          </h2>
          <ScrollableTable aria-label={`${selected?.competition} ${selected?.season} 最新价格`}>
            <table className="w-full min-w-[40rem] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th scope="col" className="p-2">球队</th>
                  <th scope="col" className="p-2">隐含概率</th>
                  <th scope="col" className="p-2">价格</th>
                  <th scope="col" className="p-2">流动性</th>
                  <th scope="col" className="p-2">成交量</th>
                  <th scope="col" className="p-2">采集时间</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s) => (
                  <tr key={s.id} className="border-b border-border/50">
                    <td className="p-2">{s.team ?? "—"}</td>
                    <td className="p-2 tabular-nums">{s.implied_prob.toFixed(4)}</td>
                    <td className="p-2 tabular-nums">
                      {s.price !== null ? s.price.toFixed(4) : "—"}
                    </td>
                    <td className="p-2 tabular-nums">{s.liquidity ?? "—"}</td>
                    <td className="p-2 tabular-nums">{s.volume ?? "—"}</td>
                    <td className="p-2 tabular-nums">{s.captured_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollableTable>
        </div>
      )}

      <FuturesLegsTable
        competition={selected?.competition ?? null}
        season={selected?.season ?? null}
      />
    </div>
  );
}

function CoveragePanel({
  coverage,
  error,
}: {
  coverage: NonNullable<ReturnType<typeof useFuturesCoverage>["data"]>;
  error: unknown;
}) {
  if (error) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="coverage-error">
        覆盖报告暂不可用
      </p>
    );
  }
  return (
    <section
      data-testid="futures-coverage"
      className="space-y-3 rounded-lg border border-border p-4"
    >
      <h2 className="text-sm font-semibold">系列前缀与覆盖</h2>
      <p className="text-xs text-muted-foreground">
        已注册 {coverage.series_registry.length} 个 series 前缀 · 竞赛{" "}
        {coverage.registered_competitions.join(", ") || "—"}
        {coverage.missing_linked_competitions.length > 0 && (
          <>
            {" "}
            · 尚无已链接数据：
            <span className="text-warn">
              {coverage.missing_linked_competitions.join(", ")}
            </span>
          </>
        )}
      </p>
      <ScrollableTable aria-label="Kalshi 系列前缀注册表">
        <table className="w-full min-w-[24rem] border-collapse text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th scope="col" className="p-1.5">Prefix</th>
              <th scope="col" className="p-1.5">Competition</th>
              <th scope="col" className="p-1.5">Type</th>
            </tr>
          </thead>
          <tbody>
            {coverage.series_registry.map((s) => (
              <tr
                key={s.series_prefix}
                className="border-b border-border/50"
                data-testid={`series-row-${s.series_prefix}`}
              >
                <td className="p-1.5 font-mono">{s.series_prefix}</td>
                <td className="p-1.5">{s.competition}</td>
                <td className="p-1.5">{s.championship_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollableTable>
      {coverage.pair_count > 0 && (
        <p className="text-xs text-muted-foreground">
          已链接 pair {coverage.pair_count}
          {Object.keys(coverage.status_counts).length > 0 && (
            <>
              {" "}
              · 状态{" "}
              {Object.entries(coverage.status_counts)
                .map(([k, v]) => `${k}:${v}`)
                .join(" ")}
            </>
          )}
        </p>
      )}
    </section>
  );
}

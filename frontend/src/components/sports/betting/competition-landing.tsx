"use client";

import { useMemo } from "react";
import Link from "next/link";
import {
  adapterLikelyLabel,
  mergeCompetitionsWithLive,
  statusLabel,
  type BettingCompetition,
} from "@/lib/betting/competition-catalog";
import { useBettingCatalog, useMatches } from "@/lib/sports-api";

type Props = {
  competition: BettingCompetition;
};

function kernelListHref(competition: BettingCompetition): string {
  const params = new URLSearchParams();
  if (competition.kernelSport) params.set("sport", competition.kernelSport);
  if (competition.competitionCode) {
    params.set("competition", competition.competitionCode);
  }
  const qs = params.toString();
  return qs ? `/sports?${qs}` : "/sports";
}

export function CompetitionLanding({ competition: staticComp }: Props) {
  const { data: liveCatalog, error: catalogError, isLoading: catalogLoading } =
    useBettingCatalog();

  const competition = useMemo(() => {
    const merged = mergeCompetitionsWithLive(
      [staticComp],
      liveCatalog?.competitions,
    );
    return merged[0] ?? staticComp;
  }, [staticComp, liveCatalog?.competitions]);

  const listHref = kernelListHref(competition);
  const shouldPollMatches =
    competition.track === "kernel" && competition.status !== "coming_soon";

  const {
    data: matches,
    error: matchesError,
    isLoading: matchesLoading,
  } = useMatches(
    shouldPollMatches
      ? {
          sport: competition.kernelSport ?? null,
          competition: competition.competitionCode ?? null,
        }
      : null,
  );

  const matchCount = matches?.length ?? 0;
  const liveReady = Boolean(liveCatalog && !catalogError);
  const adapterLabel = adapterLikelyLabel(competition.adapterLikely);

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground">
          <Link
            href="/sports/betting"
            className="text-primary underline underline-offset-2"
          >
            竞猜中心
          </Link>
          <span className="mx-1.5">/</span>
          <span>{competition.shortLabel}</span>
        </p>
        <h1 className="text-2xl font-bold">{competition.label}</h1>
        <p className="text-sm text-muted-foreground">{competition.description}</p>
        <p className="text-xs text-muted-foreground">
          状态：{statusLabel(competition.status)} · track={competition.track}
          {competition.competitionCode
            ? ` · competition=${competition.competitionCode}`
            : null}
        </p>
        {adapterLabel ? (
          <p
            className="text-xs"
            data-testid="landing-adapter-status"
            data-adapter-likely={
              competition.adapterLikely === true ? "true" : "false"
            }
          >
            数据源：
            <span
              className={
                competition.adapterLikely === true
                  ? "text-amber-800 dark:text-amber-300"
                  : "text-muted-foreground"
              }
            >
              {adapterLabel}
            </span>
            {catalogLoading && !liveCatalog ? "（同步 catalog 中…）" : null}
            {!catalogLoading && !liveReady
              ? "（本地静态；后端 catalog 不可用）"
              : null}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground" data-testid="landing-adapter-status">
            {catalogLoading && !liveCatalog
              ? "正在同步后端 catalog…"
              : liveReady
                ? "已合并后端 catalog"
                : "使用本地静态 catalog"}
          </p>
        )}
      </div>

      {competition.status === "coming_soon" && (
        <div
          role="status"
          className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
        >
          该赛道尚未接入真实赛程与盘口。不会展示占位赔率或模拟结果。数据源与结算规则确定后，将复用与
          Kernel 相同的「比赛 → 预测 → Edge → 结算」工作流。详见{" "}
          <code className="rounded bg-muted px-1">docs/dev/ESPORTS_BOUNDARY.md</code>
          。
        </div>
      )}

      {competition.track === "world_cup" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            世界杯使用独立专题 API 与赛程库，与 Kernel 多体育列表分离。
          </p>
          <Link
            href={competition.href}
            className="inline-flex rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            进入世界杯专题
          </Link>
        </div>
      )}

      {competition.track === "kernel" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            本竞赛走 Kernel 多体育预测流水线
            {competition.kernelSport
              ? `（运动：${competition.kernelSport}`
              : ""}
            {competition.competitionCode
              ? `${competition.kernelSport ? " · " : "（"}联赛：${competition.competitionCode}`
              : ""}
            {competition.kernelSport || competition.competitionCode ? "）" : ""}
            。列表 API：{" "}
            <code className="rounded bg-muted px-1 text-[11px]">
              GET /api/predictions/matches
              {competition.kernelSport || competition.competitionCode
                ? "?"
                : ""}
              {[
                competition.kernelSport
                  ? `sport=${competition.kernelSport}`
                  : null,
                competition.competitionCode
                  ? `competition=${competition.competitionCode}`
                  : null,
              ]
                .filter(Boolean)
                .join("&")}
            </code>
          </p>

          <div
            className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-sm"
            data-testid="landing-match-count"
          >
            {matchesLoading ? (
              <span className="text-muted-foreground">正在查询今日赛程…</span>
            ) : matchesError ? (
              <span className="text-muted-foreground">
                今日赛程暂不可用
                {matchesError instanceof Error
                  ? `（${matchesError.message.slice(0, 80)}）`
                  : ""}
                。常见原因：Kernel flag 关闭、adapter 未注册、或尚未 ingest。
              </span>
            ) : (
              <span>
                今日可见比赛：
                <strong className="mx-1" data-testid="landing-match-count-n">
                  {matchCount}
                </strong>
                场
                {matchCount === 0 ? (
                  <span className="ml-1 text-muted-foreground">
                    （过滤结果为空：检查 flag、sync_schedule 与数据源）
                  </span>
                ) : null}
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <Link
              href={listHref}
              className="inline-flex rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              打开 Kernel 赛程
            </Link>
            <Link
              href="/sports/edges"
              className="inline-flex rounded-md border border-border px-4 py-2 text-sm"
            >
              查看体育 Edge
            </Link>
          </div>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        需要学习校准、参数优化或市场桥接时，可从{" "}
        <Link href="/sports/betting" className="text-primary underline">
          竞猜中心 · 分析工具
        </Link>{" "}
        进入。联赛赛程启用说明见 ops RUNBOOK「Betting / 联赛赛程」。
      </p>
    </main>
  );
}

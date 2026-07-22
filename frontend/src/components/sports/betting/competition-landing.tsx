"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  adapterLikelyLabel,
  mergeCompetitionsWithLive,
  statusLabel,
  type BettingCompetition,
} from "@/lib/betting/competition-catalog";
import {
  hasOperatorApiKey,
  OPERATOR_CREDENTIALS_EVENT,
  requestOpenOperatorKey,
} from "@/lib/operator-credentials";
import {
  syncSchedule,
  useBettingCatalog,
  useBettingStatus,
  useMatches,
} from "@/lib/sports-api";

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

/** Prefixes that belong to this competition (epl → epl-, serie_a → seriea-). */
function prefixesForCompetition(
  competitionCode: string | undefined,
  registered: string[],
): string[] {
  if (!competitionCode || !registered.length) return [];
  const norm = competitionCode.toLowerCase().replace(/-/g, "_");
  // Fixture prefixes use compact codes (seriea-, ligue1-).
  const compact = norm.replace(/_/g, "");
  return registered.filter((p) => {
    const stem = p.replace(/-$/, "").toLowerCase().replace(/_/g, "");
    return stem === compact || stem === norm.replace(/_/g, "");
  });
}

export function CompetitionLanding({ competition: staticComp }: Props) {
  const { data: liveCatalog, error: catalogError, isLoading: catalogLoading } =
    useBettingCatalog();
  const { data: runtimeStatus } = useBettingStatus();

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
    mutate: mutateMatches,
  } = useMatches(
    shouldPollMatches
      ? {
          sport: competition.kernelSport ?? null,
          competition: competition.competitionCode ?? null,
        }
      : null,
  );

  const matchCount = matches?.length ?? 0;
  const preview = (matches ?? []).slice(0, 5);
  const liveReady = Boolean(liveCatalog && !catalogError);
  const adapterLabel = adapterLikelyLabel(competition.adapterLikely);
  const wiredPrefixes = prefixesForCompetition(
    competition.competitionCode,
    runtimeStatus?.registered_prefixes ?? [],
  );

  const [syncBusy, setSyncBusy] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [operatorReady, setOperatorReady] = useState(false);

  useEffect(() => {
    const refresh = () => setOperatorReady(hasOperatorApiKey());
    refresh();
    window.addEventListener(OPERATOR_CREDENTIALS_EVENT, refresh);
    return () => window.removeEventListener(OPERATOR_CREDENTIALS_EVENT, refresh);
  }, []);

  const canSync = shouldPollMatches && operatorReady;

  async function onSyncSchedule() {
    if (!canSync || syncBusy) return;
    setSyncBusy(true);
    setSyncMessage(null);
    try {
      const result = await syncSchedule({
        sport: competition.kernelSport ?? null,
        competition: competition.competitionCode ?? null,
      });
      setSyncMessage(`已同步 ${result.synced} 条（adapter 返回计数）`);
      await mutateMatches();
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : "同步失败");
    } finally {
      setSyncBusy(false);
    }
  }

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
          <p
            className="text-xs text-muted-foreground"
            data-testid="landing-adapter-status"
          >
            {catalogLoading && !liveCatalog
              ? "正在同步后端 catalog…"
              : liveReady
                ? "已合并后端 catalog"
                : "使用本地静态 catalog"}
          </p>
        )}
        {shouldPollMatches && runtimeStatus ? (
          <p
            className="text-xs text-muted-foreground"
            data-testid="landing-runtime-prefix"
          >
            Runtime：
            {runtimeStatus.kernel_ready ? "Kernel ready" : "Kernel not ready"}
            {wiredPrefixes.length
              ? ` · 本联赛 adapter：${wiredPrefixes.join(", ")}`
              : competition.competitionCode
                ? " · 本联赛 adapter 未注册（检查 flag）"
                : null}
          </p>
        ) : null}
      </div>

      {competition.status === "coming_soon" && (
        <div
          role="status"
          className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
        >
          该赛道尚未接入真实赛程与盘口。不会展示占位赔率或模拟结果。数据源与结算规则确定后，将复用与
          Kernel 相同的「比赛 → 预测 → Edge → 结算」工作流。详见{" "}
          <code className="rounded bg-muted px-1">
            docs/dev/ESPORTS_BOUNDARY.md
          </code>
          。
        </div>
      )}

      {competition.id === "lol" ? (
        <div
          className="space-y-2 rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm"
          data-testid="lol-dry-run-ops"
        >
          <p className="font-medium text-foreground">LoL dry-run 门禁（操作员）</p>
          <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
            <li>
              PHASE_LOL_ENABLED=
              {liveCatalog?.flags?.phase_lol_enabled ? "ON" : "OFF"}
              {" · "}
              LOL_DRY_RUN_IMPORT=
              {liveCatalog?.flags?.lol_dry_run_import ? "ON" : "OFF"}
              {" · "}
              path_configured=
              {liveCatalog?.flags?.lol_dry_run_path_configured ? "yes" : "no"}
            </li>
            <li>
              Runtime prefix：
              {(runtimeStatus?.registered_prefixes ?? []).includes("lol-")
                ? "lol- 已注册"
                : "lol- 未注册（需 Kernel + PHASE_LOL）"}
            </li>
            <li>
              生产 HTTP 赛程源在 GATES P2/P3/P6 关闭前禁止合并；本地仅
              dry-run JSON / Null source。
            </li>
            <li>
              同步：POST /api/predictions/schedule/sync?sport=lol（需写密钥；无假盘）。
            </li>
          </ul>
          <p className="text-xs text-muted-foreground">
            详见{" "}
            <code className="rounded bg-muted px-1">docs/dev/lol/GATES.md</code>
            {" · "}
            <code className="rounded bg-muted px-1">
              docs/dev/adr/005-lol-data-vendor-selection.md
            </code>
          </p>
        </div>
      ) : null}

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

          {preview.length > 0 ? (
            <ul
              className="divide-y divide-border rounded-lg border border-border"
              data-testid="landing-match-preview"
            >
              {preview.map((m) => (
                <li key={m.match_id}>
                  <Link
                    href={`/sports/${encodeURIComponent(m.match_id)}`}
                    className="flex items-center justify-between gap-2 px-3 py-2 text-sm hover:bg-secondary/40"
                  >
                    <span>
                      {m.home_team} vs {m.away_team}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {m.has_prediction ? "已预测" : "待预测"}
                    </span>
                  </Link>
                </li>
              ))}
              {matchCount > preview.length ? (
                <li className="px-3 py-2 text-xs text-muted-foreground">
                  另有 {matchCount - preview.length} 场 — 打开完整列表查看
                </li>
              ) : null}
            </ul>
          ) : null}

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
            {canSync ? (
              <button
                type="button"
                data-testid="landing-sync-schedule"
                disabled={syncBusy}
                onClick={() => void onSyncSchedule()}
                className="inline-flex rounded-md border border-border px-4 py-2 text-sm disabled:opacity-50"
              >
                {syncBusy ? "同步中…" : "同步赛程"}
              </button>
            ) : null}
          </div>
          {syncMessage ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid="landing-sync-msg"
            >
              {syncMessage}
            </p>
          ) : null}
          {!operatorReady && shouldPollMatches ? (
            <p className="text-xs text-muted-foreground">
              配置 session 中的 operator API 写密钥后可使用「同步赛程」（POST
              /api/predictions/schedule/sync）。{" "}
              <a
                href="#operator-key"
                className="text-primary underline underline-offset-2"
                data-testid="landing-open-operator-key"
                onClick={(e) => {
                  e.preventDefault();
                  requestOpenOperatorKey({ setHash: true });
                }}
              >
                打开顶部授权
              </a>
            </p>
          ) : null}
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

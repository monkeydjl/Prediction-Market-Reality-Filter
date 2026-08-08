"use client";

import { useMemo } from "react";
import Link from "next/link";
import {
  BETTING_COMPETITIONS,
  BETTING_TOOL_LINKS,
  SECTION_LABELS,
  adapterLikelyLabel,
  mergeCompetitionsWithLive,
  statusLabel,
  type BettingCompetition,
  type CompetitionSection,
} from "@/lib/betting/competition-catalog";
import { useBettingCatalog, useBettingStatus } from "@/lib/sports-api";

const SECTION_ORDER: CompetitionSection[] = [
  "football",
  "americas",
  "esports",
  "tools",
];

function StatusBadge({ status }: { status: BettingCompetition["status"] }) {
  const tone =
    status === "live"
      ? "bg-emerald-500/15 text-emerald-400"
      : status === "kernel"
        ? "bg-sky-500/15 text-sky-400"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {statusLabel(status)}
    </span>
  );
}

function AdapterBadge({ likely }: { likely: boolean | undefined }) {
  const label = adapterLikelyLabel(likely);
  if (!label) return null;
  const tone =
    likely === true
      ? "bg-amber-500/15 text-amber-300"
      : "bg-muted text-muted-foreground";
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}
      data-testid="adapter-likely-badge"
      data-adapter-likely={likely === true ? "true" : "false"}
    >
      {label}
    </span>
  );
}

function CompetitionCard({ item }: { item: BettingCompetition }) {
  const href =
    item.status === "coming_soon"
      ? `/sports/betting/${item.id}`
      : item.track === "kernel"
        ? `/sports/betting/${item.id}`
        : item.href;

  return (
    <Link
      href={href}
      className="group rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
      data-testid={`betting-comp-${item.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold group-hover:text-primary">{item.label}</h3>
        <div className="flex flex-wrap justify-end gap-1">
          <StatusBadge status={item.status} />
          <AdapterBadge likely={item.adapterLikely} />
        </div>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{item.description}</p>
    </Link>
  );
}

export default function BettingHubPage() {
  const { data: liveCatalog, error, isLoading } = useBettingCatalog();
  const { data: runtimeStatus } = useBettingStatus();

  const competitions = useMemo(
    () =>
      mergeCompetitionsWithLive(
        BETTING_COMPETITIONS,
        liveCatalog?.competitions,
      ),
    [liveCatalog?.competitions],
  );

  const bySection = useMemo(() => {
    const map = new Map<CompetitionSection, BettingCompetition[]>();
    for (const section of SECTION_ORDER) {
      if (section === "tools") continue;
      map.set(
        section,
        competitions.filter((c) => c.section === section),
      );
    }
    return map;
  }, [competitions]);

  const flags = liveCatalog?.flags;
  const liveReady = Boolean(liveCatalog && !error);
  const prefixes = runtimeStatus?.registered_prefixes ?? [];
  const prefixPreview =
    prefixes.length > 0
      ? prefixes.slice(0, 8).join(", ") + (prefixes.length > 8 ? "…" : "")
      : null;

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">竞猜中心</h1>
        <p className="text-sm text-muted-foreground">
          统一入口覆盖世界杯专题、五大联赛 / NBA 等 Kernel 赛程，以及 Edge
          与推荐工具。电竞等赛道先占位，不展示假盘口。
        </p>
      </div>

      <div
        role="note"
        className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">双轨说明</span>
          ：世界杯走{" "}
          <code className="rounded bg-muted px-1">/api/world-cup/*</code>
          ；NBA / MLB / NHL / 足球联赛走 Kernel{" "}
          <code className="rounded bg-muted px-1">/api/predictions/*</code>
          。二者数据与结算路径不同，卡片上会标明状态。
        </p>
        <p className="mt-1.5" data-testid="catalog-source-hint">
          {isLoading && !liveCatalog
            ? "正在同步后端 catalog…"
            : liveReady
              ? "已合并后端 catalog（含数据源接线提示）。"
              : "使用本地静态 catalog（后端不可用或未启动时仍可浏览入口）。"}
          {liveReady && flags ? (
            <span data-testid="hub-flag-strip">
              {" "}
              Kernel=
              {flags.kernel_prediction_enabled ? "ON" : "OFF"}
              {" · "}
              EPL={flags.epl_data_enabled ? "ON" : "OFF"}
              {" · "}
              五大联赛={flags.phase2_leagues_enabled ? "ON" : "OFF"}
              {" · "}
              LoL={flags.phase_lol_enabled ? "ON" : "OFF"}
              {flags.phase_lol_enabled
                ? ` · dry-run=${flags.lol_dry_run_import ? "ON" : "OFF"}`
                : null}
            </span>
          ) : null}
        </p>
        {runtimeStatus ? (
          <p className="mt-1.5" data-testid="hub-runtime-status">
            Runtime：
            {runtimeStatus.kernel_ready ? "Kernel ready" : "Kernel not ready"}
            {prefixPreview ? ` · prefixes: ${prefixPreview}` : null}
            {runtimeStatus.lol
              ? ` · LoL vendor=${runtimeStatus.lol.schedule_vendor ?? "null"}→${runtimeStatus.lol.effective_schedule_vendor ?? "null"}${runtimeStatus.lol.schedule_source_blocked ? " (blocked)" : ""}`
              : null}
          </p>
        ) : null}
      </div>

      {SECTION_ORDER.map((section) => {
        if (section === "tools") {
          return (
            <section
              key={section}
              className="space-y-3"
              aria-labelledby={`sec-${section}`}
            >
              <h2 id={`sec-${section}`} className="text-lg font-semibold">
                {SECTION_LABELS[section]}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {BETTING_TOOL_LINKS.map((tool) => (
                  <Link
                    key={tool.id}
                    href={tool.href}
                    className="group rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
                    data-testid={`betting-tool-${tool.id}`}
                  >
                    <h3 className="font-semibold group-hover:text-primary">
                      {tool.title}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {tool.description}
                    </p>
                  </Link>
                ))}
              </div>
            </section>
          );
        }

        const items = bySection.get(section) ?? [];
        if (items.length === 0) return null;

        return (
          <section
            key={section}
            className="space-y-3"
            aria-labelledby={`sec-${section}`}
          >
            <h2 id={`sec-${section}`} className="text-lg font-semibold">
              {SECTION_LABELS[section]}
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {items.map((item) => (
                <CompetitionCard key={item.id} item={item} />
              ))}
            </div>
          </section>
        );
      })}
    </main>
  );
}

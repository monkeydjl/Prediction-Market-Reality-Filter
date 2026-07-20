import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getCompetitionById,
  statusLabel,
} from "@/lib/betting/competition-catalog";

type PageProps = {
  params: Promise<{ competitionId: string }>;
};

export default async function BettingCompetitionPage({ params }: PageProps) {
  const { competitionId } = await params;
  const competition = getCompetitionById(competitionId);
  if (!competition) {
    notFound();
  }

  const kernelListHref = competition.kernelSport
    ? `/sports?sport=${encodeURIComponent(competition.kernelSport)}`
    : "/sports";

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
        </p>
      </div>

      {competition.status === "coming_soon" && (
        <div
          role="status"
          className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
        >
          该赛道尚未接入真实赛程与盘口。不会展示占位赔率或模拟结果。数据源与结算规则确定后，将复用与
          Kernel 相同的「比赛 → 预测 → Edge → 结算」工作流。
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
              ? `（运动筛选：${competition.kernelSport}）`
              : ""}
            。联赛级细粒度 competition 过滤将在数据源齐全后加强；当前先进入对应运动的赛程列表。
          </p>
          <div className="flex flex-wrap gap-2">
            <Link
              href={kernelListHref}
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
        进入。
      </p>
    </main>
  );
}

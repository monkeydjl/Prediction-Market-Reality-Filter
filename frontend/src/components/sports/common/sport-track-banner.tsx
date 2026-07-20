"use client";

import Link from "next/link";

export type SportTrack = "kernel" | "world_cup";

interface SportTrackBannerProps {
  track: SportTrack;
  testId?: string;
}

/**
 * Clarifies Kernel multi-sport match list vs World Cup tournament special.
 * Different data paths / APIs — not interchangeable.
 */
export function SportTrackBanner({
  track,
  testId = "sport-track-banner",
}: SportTrackBannerProps) {
  if (track === "kernel") {
    return (
      <div
        data-testid={testId}
        role="note"
        className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">Kernel 多体育赛程</span>
          ：NBA / MLB / NHL / 足球等联赛的通用比赛预测（引擎、因子、学习闭环、结算）。
          API 前缀{" "}
          <code className="rounded bg-muted px-1">/api/sports/*</code>
          ，与世界杯专题
          <strong className="font-medium text-foreground"> 不是同一套数据</strong>
          。
        </p>
        <p className="mt-1">
          统一赛事入口见{" "}
          <Link
            href="/sports/betting"
            className="text-primary underline underline-offset-2"
          >
            竞猜中心
          </Link>
          ；世界杯小组赛 / 淘汰赛 / 夺冠概率请到{" "}
          <Link
            href="/sports/world-cup"
            className="text-primary underline underline-offset-2"
          >
            世界杯专题
          </Link>
          。
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid={testId}
      role="note"
      className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
    >
      <p>
        <span className="font-medium text-foreground">世界杯专题</span>
        ：2026 世界杯赛程、小组出线、淘汰赛树与夺冠模拟。
        使用{" "}
        <code className="rounded bg-muted px-1">/api/world-cup/*</code>
        （及专用预测库），
        <strong className="font-medium text-foreground"> 不是</strong>
        Kernel 多体育列表里的单场比赛流水线。
      </p>
      <p className="mt-1">
        其它联赛与工具见{" "}
        <Link
          href="/sports/betting"
          className="text-primary underline underline-offset-2"
        >
          竞猜中心
        </Link>
        ；NBA 等 Kernel 赛程见{" "}
        <Link href="/sports" className="text-primary underline underline-offset-2">
          体育预测
        </Link>
        ；学习校准见{" "}
        <Link
          href="/sports/learning"
          className="text-primary underline underline-offset-2"
        >
          学习仪表盘
        </Link>
        。
      </p>
    </div>
  );
}

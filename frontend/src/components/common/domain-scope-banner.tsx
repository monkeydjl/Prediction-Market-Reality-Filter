"use client";

import Link from "next/link";

export type DomainScope = "event" | "sport";

interface DomainScopeBannerProps {
  domain: DomainScope;
  /** Optional override of the counterpart path */
  counterpartHref?: string;
  testId?: string;
}

/**
 * Clarifies Event Intelligence vs Sports Prediction OS scopes on similarly named pages
 * (e.g. /edges vs /sports/edges).
 */
export function DomainScopeBanner({
  domain,
  counterpartHref,
  testId = "domain-scope-banner",
}: DomainScopeBannerProps) {
  if (domain === "event") {
    const href = counterpartHref ?? "/sports/edges";
    return (
      <div
        data-testid={testId}
        role="note"
        className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">事件情报 Edge</span>
          ：比较事件分析概率与预测市场（Polymarket 等）共识价的分歧生命周期。
          数据来自事件历史快照，
          <strong className="font-medium text-foreground">不是</strong>
          体育 Kernel 的比赛模型 vs 盘口。
        </p>
        <p className="mt-1">
          体育比赛模型偏离请到{" "}
          <Link href={href} className="text-primary underline underline-offset-2">
            体育 · Edge 偏离
          </Link>
          。
        </p>
      </div>
    );
  }

  const href = counterpartHref ?? "/edges";
  return (
    <div
      data-testid={testId}
      role="note"
      className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
    >
      <p>
        <span className="font-medium text-foreground">体育 Edge 偏离</span>
        ：比较 Kernel 引擎对比赛结果的预测概率与链上/博彩市场隐含概率
        （需 Phase7 Edge 检测器与市场桥接）。
        <strong className="font-medium text-foreground"> 不是</strong>
        事件情报平台里的 Polymarket 事件 edge。
      </p>
      <p className="mt-1">
        事件 / 预测市场分歧监测请到{" "}
        <Link href={href} className="text-primary underline underline-offset-2">
          事件 · Edge 监测
        </Link>
        。
      </p>
    </div>
  );
}

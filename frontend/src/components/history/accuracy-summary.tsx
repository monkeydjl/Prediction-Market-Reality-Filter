import Link from "next/link";
import { Gauge, ListChecks, Target } from "lucide-react";
import type { CalibrationAgg } from "@/lib/api";

export function AccuracySummary({ overall }: { overall: CalibrationAgg }) {
  const hasData = overall.n > 0 && overall.brier_score != null;
  const skill = overall.skill_score;

  const cards = [
    {
      label: "技巧分数",
      value: hasData && skill != null ? skill.toFixed(2) : "—",
      hint: ">0 优于随机猜测，越高越好",
      icon: <Target className="size-4" aria-hidden="true" />,
      tone: hasData && skill != null && skill > 0 ? "text-pos" : "text-muted-foreground",
    },
    {
      label: "已结算样本",
      value: String(overall.n),
      hint: "纳入事件层校准的已结算事件",
      icon: <ListChecks className="size-4" aria-hidden="true" />,
      tone: "text-foreground",
    },
    {
      label: "平均 Brier 分数",
      value: hasData && overall.brier_score != null ? overall.brier_score.toFixed(3) : "—",
      hint: "越低代表概率校准越好",
      icon: <Gauge className="size-4" aria-hidden="true" />,
      tone:
        hasData && overall.brier_score != null && overall.brier_score < 0.2
          ? "text-pos"
          : "text-warn",
    },
  ];

  return (
    <div className="flex flex-col gap-3" data-testid="accuracy-summary-wrap">
      <div className="rounded-lg border border-border bg-card px-4 py-3 text-xs leading-relaxed text-muted-foreground">
        <p className="font-medium text-foreground">事件层校准（历史复盘）</p>
        <p className="mt-1">
          衡量「已结算事件的最终概率估计」是否准——来自事件情报闭环（EIP），
          <strong className="font-medium text-foreground">不是</strong>
          体育 Kernel 的引擎校准参数。
        </p>
        <p className="mt-1">
          体育引擎 Brier / 可靠性曲线请到{" "}
          <Link href="/sports/learning?tab=calibration" className="text-primary underline">
            学习 → 校准诊断
          </Link>
          ；市场结算反馈见{" "}
          <Link href="/sports/settlements" className="text-primary underline">
            市场结算
          </Link>
          。
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {cards.map((c) => (
          <div
            key={c.label}
            className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4"
          >
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={c.tone}>{c.icon}</span>
              {c.label}
            </div>
            <div className={`font-mono text-3xl font-semibold tabular-nums ${c.tone}`}>
              {c.value}
            </div>
            <div className="text-xs text-muted-foreground">{c.hint}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

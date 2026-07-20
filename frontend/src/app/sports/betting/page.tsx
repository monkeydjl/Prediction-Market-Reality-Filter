import Link from "next/link";
import { Globe, Trophy, Medal, Crosshair, Lightbulb } from "lucide-react";

const BETTING_CATEGORIES = [
  {
    href: "/sports/world-cup",
    title: "世界杯竞猜",
    description: "赛程、分组、出线概率、淘汰赛对阵、夺冠概率预测",
    icon: Trophy,
    color: "text-amber-600",
  },
  {
    href: "/sports",
    title: "每日比赛预测",
    description: "NBA / MLB / NHL 等赛事的 AI 概率预测与因子分解",
    icon: Medal,
    color: "text-blue-600",
  },
  {
    href: "/sports/edges",
    title: "Edge 偏离竞猜",
    description: "模型预测与市场概率最大偏离的比赛，发现价值投注机会",
    icon: Crosshair,
    color: "text-green-600",
  },
  {
    href: "/sports/recommendations",
    title: "智能推荐",
    description: "基于决策缺口和市场偏离的智能竞猜推荐",
    icon: Lightbulb,
    color: "text-purple-600",
  },
  {
    href: "/sports/futures",
    title: "期货/冠军竞猜",
    description: "赛季级别的冠军和期货市场概率预测",
    icon: Globe,
    color: "text-cyan-600",
  },
];

export default function BettingHubPage() {
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">竞猜中心</h1>
        <p className="text-sm text-muted-foreground">
          选择竞猜类型，查看 AI 预测和市场分析，发现价值机会。
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {BETTING_CATEGORIES.map((cat) => {
          const Icon = cat.icon;
          return (
            <Link
              key={cat.href}
              href={cat.href}
              className="group rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
            >
              <div className="flex items-start gap-3">
                <span className={`mt-0.5 ${cat.color}`}>
                  <Icon className="size-6" aria-hidden="true" />
                </span>
                <div className="min-w-0 space-y-1">
                  <h2 className="font-semibold group-hover:text-primary">
                    {cat.title}
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    {cat.description}
                  </p>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </main>
  );
}

"use client";
import { FuturesDashboard } from "@/components/sports/futures/FuturesDashboard";

export default function FuturesPage() {
  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">期货 / 冠军市场</h1>
        <p className="text-sm text-muted-foreground">
          多腿系列（NBA 冠军、World Series、Super Bowl 等）隐含概率与覆盖完整性，
          区别于单场比赛 binary 市场。
        </p>
      </div>
      <FuturesDashboard />
    </main>
  );
}

"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { FuturesDashboard } from "@/components/sports/futures/FuturesDashboard";

/**
 * A futures pair is keyed by (competition, season), both runtime values, so the
 * detail deep link travels as `?competition=&season=` under `output: "export"`
 * (OQ-3) rather than a path segment. `useSearchParams` needs a Suspense
 * boundary to prerender — same shape as `/sports/match`.
 */
function FuturesPageInner() {
  const params = useSearchParams();
  return (
    <FuturesDashboard
      competition={params.get("competition")}
      season={params.get("season")}
    />
  );
}

export default function FuturesPage() {
  return (
    <main id="main-content" className="mx-auto max-w-5xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">期货 / 冠军市场</h1>
        <p className="text-sm text-muted-foreground">
          多腿系列（NBA 冠军、World Series、Super Bowl 等）隐含概率与覆盖完整性，
          区别于单场比赛 binary 市场。
        </p>
      </div>
      <Suspense fallback={<p className="text-sm text-muted-foreground">加载中...</p>}>
        <FuturesPageInner />
      </Suspense>
    </main>
  );
}

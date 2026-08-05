"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PredictionTrajectory } from "@/components/sports/learning/prediction-trajectory";

function MatchTrajectoryInner() {
  const searchParams = useSearchParams();
  const matchId = searchParams.get("matchId") ?? "";

  if (!matchId) {
    return (
      <div className="space-y-4">
        <p className="text-muted-foreground">缺少比赛 ID</p>
        <Link href="/sports/learning" className="text-primary hover:underline">
          返回学习中心
        </Link>
      </div>
    );
  }

  return <PredictionTrajectory matchId={matchId} />;
}

/**
 * Match ids are runtime-only, so this page takes `?matchId=` rather than a
 * dynamic segment — see `@/lib/sports-routes` for why the static export needs
 * it. `useSearchParams` requires a Suspense boundary to prerender.
 */
export default function MatchTrajectoryPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <Suspense
        fallback={
          <div>
            <div className="h-4 w-24 animate-pulse rounded bg-muted" />
            <div className="mt-4 h-8 w-64 animate-pulse rounded bg-muted" />
            <div className="mt-6 h-64 w-full animate-pulse rounded bg-muted" />
          </div>
        }
      >
        <MatchTrajectoryInner />
      </Suspense>
    </main>
  );
}

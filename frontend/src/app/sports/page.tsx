"use client";

import { useState } from "react";
import Link from "next/link";
import { SportFilter } from "@/components/sports/common/sport-filter";
import { MatchListCard } from "@/components/sports/common/match-list-card";
import { SportTrackBanner } from "@/components/sports/common/sport-track-banner";
import { useMatches } from "@/lib/sports-api";

export default function SportsPage() {
  const [sport, setSport] = useState<string | null>(null);
  const { data, error, isLoading, mutate } = useMatches(sport ?? undefined);
  const matches = data ?? [];

  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">体育预测（Kernel）</h1>
        <p className="text-sm text-muted-foreground">
          多联赛比赛列表与引擎预测。世界杯小组 / 淘汰赛专题请使用{" "}
          <Link href="/sports/world-cup" className="text-primary underline">
            世界杯专题
          </Link>
          。
        </p>
      </div>

      <SportTrackBanner track="kernel" />

      <SportFilter value={sport} onChange={setSport} />

      {isLoading && <p className="text-muted-foreground">加载中...</p>}

      {errorMessage && (
        <div className="space-y-2">
          <p className="text-destructive">加载失败: {errorMessage}</p>
          <button
            type="button"
            onClick={() => mutate()}
            className="rounded-md border px-3 py-1.5 text-sm"
          >
            重试
          </button>
        </div>
      )}

      {!isLoading && !errorMessage && matches.length === 0 && (
        <p className="text-muted-foreground">今日无比赛</p>
      )}

      {!isLoading && !errorMessage && matches.length > 0 && (
        <div className="space-y-3">
          {matches.map((match) => (
            <MatchListCard key={match.match_id} match={match} />
          ))}
        </div>
      )}
    </main>
  );
}

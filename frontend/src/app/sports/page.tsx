"use client";

import { useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { SportFilter } from "@/components/sports/common/sport-filter";
import { CompetitionChips } from "@/components/sports/common/competition-chips";
import { MatchListCard } from "@/components/sports/common/match-list-card";
import { SportTrackBanner } from "@/components/sports/common/sport-track-banner";
import {
  getCompetitionByCode,
  normalizeCompetitionCode,
} from "@/lib/betting/competition-catalog";
import { useMatches } from "@/lib/sports-api";

const SPORT_CODES = new Set([
  "football",
  "basketball",
  "baseball",
  "hockey",
  "lol",
]);

export default function SportsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sportParam = searchParams.get("sport");
  const competitionParam = searchParams.get("competition");
  const sport = useMemo(() => {
    if (sportParam && SPORT_CODES.has(sportParam)) return sportParam;
    return null;
  }, [sportParam]);
  const competition = useMemo(() => {
    return normalizeCompetitionCode(competitionParam);
  }, [competitionParam]);

  const competitionMeta = useMemo(
    () => getCompetitionByCode(competition),
    [competition],
  );

  const setSport = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams();
      if (next) params.set("sport", next);
      // Drop competition when user changes sport filter (avoids empty combo).
      const qs = params.toString() ? `?${params.toString()}` : "";
      router.replace(`/sports${qs}`, { scroll: false });
    },
    [router],
  );

  const setCompetition = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams();
      if (sport) params.set("sport", sport);
      if (next) params.set("competition", next);
      const qs = params.toString() ? `?${params.toString()}` : "";
      router.replace(`/sports${qs}`, { scroll: false });
    },
    [router, sport],
  );

  const { data, error, isLoading, mutate } = useMatches({
    sport,
    competition,
  });
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
          多联赛比赛列表与引擎预测。统一入口见{" "}
          <Link href="/sports/betting" className="text-primary underline">
            竞猜中心
          </Link>
          ；世界杯专题见{" "}
          <Link href="/sports/world-cup" className="text-primary underline">
            世界杯
          </Link>
          。
        </p>
      </div>

      <SportTrackBanner track="kernel" />

      {competition ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="competition-filter-hint"
        >
          联赛过滤：
          <code className="rounded bg-muted px-1">{competition}</code>
          {competitionMeta ? (
            <>
              {" · "}
              <Link
                href={`/sports/betting/${competitionMeta.id}`}
                className="text-primary underline underline-offset-2"
                data-testid="competition-landing-link"
              >
                {competitionMeta.shortLabel} 落地页
              </Link>
            </>
          ) : null}
          {" · "}
          <button
            type="button"
            className="text-primary underline underline-offset-2"
            onClick={() => setCompetition(null)}
          >
            清除联赛
          </button>
          {" · "}
          <Link
            href="/sports/betting"
            className="text-primary underline underline-offset-2"
          >
            竞猜中心
          </Link>
        </p>
      ) : null}

      <SportFilter value={sport} onChange={setSport} />
      <CompetitionChips
        sport={sport}
        value={competition}
        onChange={setCompetition}
      />

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

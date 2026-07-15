"use client";

import { useEffect, useState } from "react";
import { SportFilter } from "@/components/sports/sport-filter";
import { MatchListCard } from "@/components/sports/match-list-card";
import { fetchMatches, type MatchSummary } from "@/lib/sports-api";

export default function SportsPage() {
  const [sport, setSport] = useState<string | null>(null);
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchMatches(sport ?? undefined)
      .then((data) => {
        setMatches(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [sport]);

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <h1 className="text-2xl font-bold">体育预测</h1>

      <SportFilter value={sport} onChange={setSport} />

      {loading && <p className="text-muted-foreground">加载中...</p>}

      {error && (
        <div className="space-y-2">
          <p className="text-destructive">加载失败: {error}</p>
          <button
            type="button"
            onClick={() => setSport(sport)}
            className="rounded-md border px-3 py-1.5 text-sm"
          >
            重试
          </button>
        </div>
      )}

      {!loading && !error && matches.length === 0 && (
        <p className="text-muted-foreground">今日无比赛</p>
      )}

      {!loading && !error && matches.length > 0 && (
        <div className="space-y-3">
          {matches.map((match) => (
            <MatchListCard key={match.match_id} match={match} />
          ))}
        </div>
      )}
    </main>
  );
}

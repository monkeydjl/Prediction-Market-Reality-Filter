"use client";

import { useState } from "react";
import Link from "next/link";
import { MatchPicker } from "@/components/sports/common/match-picker";
import { matchDetailHref } from "@/lib/sports-routes";
import { EdgeDetailPanel } from "./edgedetailpanel";
import { EdgeTimelineChart } from "./edgetimelinechart";

const OUTCOMES: { value: string; label: string }[] = [
  { value: "", label: "全部结果" },
  { value: "home_win", label: "主胜" },
  { value: "draw", label: "平局" },
  { value: "away_win", label: "客胜" },
];

/**
 * Match-scoped edge history: the discrepancies table answers "where is the
 * edge now", this answers "how did that edge get there".
 */
export function EdgeHistoryExplorer() {
  const [matchId, setMatchId] = useState("");
  const [outcome, setOutcome] = useState("");

  return (
    <section data-testid="edge-history-explorer" className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <MatchPicker value={matchId} onChange={setMatchId} testId="edge-history-match" />
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          结果
          <select
            data-testid="edge-history-outcome"
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            className="rounded-md border border-input bg-card px-2 py-1.5 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {OUTCOMES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {matchId && (
          <Link
            href={matchDetailHref(matchId)}
            className="pb-1.5 text-xs text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            打开比赛详情 →
          </Link>
        )}
      </div>

      {!matchId ? (
        <p data-testid="edge-history-no-match" className="text-xs text-muted-foreground">
          选择场次后可查看该场 Edge 的最新明细与历史轨迹。
        </p>
      ) : (
        <div className="space-y-4">
          <EdgeDetailPanel matchId={matchId} />
          <EdgeTimelineChart matchId={matchId} mappedOutcome={outcome || undefined} />
        </div>
      )}
    </section>
  );
}

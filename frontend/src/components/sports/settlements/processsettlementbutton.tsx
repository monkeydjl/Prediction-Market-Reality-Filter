"use client";
import { useState } from "react";
import { processSettlement } from "@/lib/sports-api";

interface Props {
  matchId: string;
  onDone?: () => void;
}

export function ProcessSettlementButton({ matchId, onDone }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  async function handleClick() {
    if (!window.confirm(`确认为比赛 ${matchId} 重新计算结算吗？`)) return;
    setLoading(true);
    setError(null);
    setOk(false);
    try {
      await processSettlement(matchId);
      setOk(true);
      onDone?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "结算失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        data-testid={`process-${matchId}`}
        onClick={handleClick}
        disabled={loading || !matchId}
        className="rounded border px-2 py-1 text-xs disabled:opacity-50"
      >
        {loading ? "计算中..." : "重算结算"}
      </button>
      {ok && (
        <span data-testid={`process-ok-${matchId}`} className="text-xs text-pos">
          已触发
        </span>
      )}
      {error && (
        <span data-testid={`process-error-${matchId}`} className="text-xs text-neg">
          {error}
        </span>
      )}
    </span>
  );
}

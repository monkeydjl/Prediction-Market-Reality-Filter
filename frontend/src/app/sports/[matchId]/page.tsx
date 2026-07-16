"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { MatchDetailPanel } from "@/components/sports/match-detail-panel";
import { TraditionalOddsChart } from "@/components/sports/markets/TraditionalOddsChart";
import {
  useMatchDetail,
  triggerPrediction,
} from "@/lib/sports-api";
import type { MatchDetail, PredictionResult } from "@/lib/sports-api";

// The global swrFetcher localizes HTTP 404 to this message via
// buildApiErrorMessage. We match on it to preserve the original
// "比赛不存在" UX that used to rely on `instanceof NotFoundError`.
const NOT_FOUND_MESSAGE = "请求的资源不存在";

type TabId = "details" | "odds";

export default function MatchDetailPage() {
  const params = useParams();
  const matchId = params.matchId as string;

  const { data, error, isLoading } = useMatchDetail(matchId);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [isPredicting, setIsPredicting] = useState(false);
  const [predictError, setPredictError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("details");

  const match: MatchDetail | null = data?.match ?? null;
  const currentPrediction = prediction ?? data?.prediction ?? null;

  const notFound =
    error instanceof Error && error.message === NOT_FOUND_MESSAGE;
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : predictError;

  const handlePredict = () => {
    setIsPredicting(true);
    setPredictError(null);
    triggerPrediction(matchId)
      .then((result) => {
        setPrediction(result);
        setIsPredicting(false);
      })
      .catch((err) => {
        setPredictError(err instanceof Error ? err.message : "预测失败");
        setIsPredicting(false);
      });
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-6 md:px-6">
        <p className="text-muted-foreground">加载中...</p>
      </main>
    );
  }

  if (notFound) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-muted-foreground">比赛不存在</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  if (errorMessage || !match) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-destructive">加载失败: {errorMessage}</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 md:px-6">
      <Link href="/sports" className="text-sm text-muted-foreground hover:underline">
        ← 返回列表
      </Link>
      <div className="flex gap-2 border-b">
        <button
          type="button"
          onClick={() => setActiveTab("details")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${
            activeTab === "details"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          比赛详情
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("odds")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${
            activeTab === "odds"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          赔率对比
        </button>
      </div>
      {activeTab === "details" ? (
        <MatchDetailPanel
          match={match}
          prediction={currentPrediction}
          onPredict={handlePredict}
          isPredicting={isPredicting}
        />
      ) : (
        <TraditionalOddsChart matchId={matchId} />
      )}
    </main>
  );
}

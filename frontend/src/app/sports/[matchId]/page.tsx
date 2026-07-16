"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { MatchDetailPanel } from "@/components/sports/match-detail-panel";
import { TraditionalOddsChart } from "@/components/sports/markets/TraditionalOddsChart";
import {
  fetchMatchDetail,
  triggerPrediction,
  NotFoundError,
  type MatchDetail,
  type PredictionResult,
} from "@/lib/sports-api";

type TabId = "details" | "odds";

export default function MatchDetailPage() {
  const params = useParams();
  const matchId = params.matchId as string;

  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [isPredicting, setIsPredicting] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("details");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchMatchDetail(matchId)
      .then((data) => {
        setMatch(data.match);
        setPrediction(data.prediction);
        setLoading(false);
      })
      .catch((err) => {
        if (err instanceof NotFoundError) {
          setNotFound(true);
        } else {
          setError(err.message);
        }
        setLoading(false);
      });
  }, [matchId]);

  const handlePredict = () => {
    setIsPredicting(true);
    triggerPrediction(matchId)
      .then((result) => {
        setPrediction(result);
        setIsPredicting(false);
      })
      .catch((err) => {
        setError(err.message);
        setIsPredicting(false);
      });
  };

  if (loading) {
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

  if (error || !match) {
    return (
      <main className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-6">
        <p className="text-destructive">加载失败: {error}</p>
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
          prediction={prediction}
          onPredict={handlePredict}
          isPredicting={isPredicting}
        />
      ) : (
        <TraditionalOddsChart matchId={matchId} />
      )}
    </main>
  );
}

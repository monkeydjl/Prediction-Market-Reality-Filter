"use client";

import { Suspense, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { MatchDetailPanel } from "@/components/sports/common/match-detail-panel";
import { TraditionalOddsChart } from "@/components/sports/markets/TraditionalOddsChart";
import { EdgeDetailPanel } from "@/components/sports/edges/edgedetailpanel";
import { EdgeTimelineChart } from "@/components/sports/edges/edgetimelinechart";
import { RealtimePriceTable } from "@/components/sports/realtime/realtimepricetable";
import { MatchRecommendationPanel } from "@/components/sports/recommendations/MatchRecommendationPanel";
import { ProcessSettlementButton } from "@/components/sports/settlements/processsettlementbutton";
import { MatchSettlementPanel } from "@/components/sports/settlements/MatchSettlementPanel";
import {
  useMatchDetail,
  useEngines,
  triggerPrediction,
} from "@/lib/sports-api";
import type { MatchDetail, PredictionResult } from "@/lib/sports-api";
import { ApiError } from "@/lib/api";
import { matchDetailHref } from "@/lib/sports-routes";

type TabId = "details" | "edge" | "odds" | "realtime";

const VALID_TABS: TabId[] = ["details", "edge", "odds", "realtime"];

function normalizeEngines(data: unknown): string[] | undefined {
  if (Array.isArray(data)) {
    return data.filter((x): x is string => typeof x === "string");
  }
  if (data && typeof data === "object" && Array.isArray((data as { engines?: unknown }).engines)) {
    return ((data as { engines: unknown[] }).engines).filter(
      (x): x is string => typeof x === "string",
    );
  }
  return undefined;
}

function MatchDetailInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const matchId = searchParams.get("id") ?? "";

  const { data, error, isLoading } = useMatchDetail(matchId || null);
  const { data: enginesRaw } = useEngines();
  const engines = normalizeEngines(enginesRaw);

  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [isPredicting, setIsPredicting] = useState(false);
  const [predictError, setPredictError] = useState<string | null>(null);
  const [selectedEngine, setSelectedEngine] = useState("auto");

  const match: MatchDetail | null = data?.match ?? null;
  const currentPrediction = prediction ?? data?.prediction ?? null;

  const notFound = error instanceof ApiError && error.status === 404;
  const serviceUnavailable =
    error instanceof ApiError && error.status === 503;
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  const tabParam = searchParams.get("tab") as TabId | null;
  const activeTab: TabId =
    tabParam && VALID_TABS.includes(tabParam) ? tabParam : "details";

  const handleTabChange = (tab: TabId) => {
    router.replace(matchDetailHref(matchId, tab));
  };

  const handlePredict = () => {
    setIsPredicting(true);
    setPredictError(null);
    triggerPrediction(matchId, selectedEngine)
      .then((result) => {
        setPrediction(result);
        setIsPredicting(false);
      })
      .catch((err) => {
        setPredictError(err instanceof Error ? err.message : "预测失败");
        setIsPredicting(false);
      });
  };

  if (!matchId) {
    return (
      <div className="space-y-4">
        <p className="text-muted-foreground">缺少比赛 ID</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </div>
    );
  }

  if (isLoading) {
    return <p className="text-muted-foreground">加载中...</p>;
  }

  if (notFound) {
    return (
      <div className="space-y-4">
        <p className="text-muted-foreground">比赛不存在</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </div>
    );
  }

  if (serviceUnavailable) {
    return (
      <div className="space-y-4">
        <p className="text-destructive">
          预测内核未启用（HTTP 503）。请在后端设置{" "}
          <code className="rounded bg-muted px-1">KERNEL_PREDICTION_ENABLED=true</code>
          {" "}后重启服务。
        </p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </div>
    );
  }

  if (errorMessage || !match) {
    return (
      <div className="space-y-4">
        <p className="text-destructive">加载失败: {errorMessage}</p>
        <Link href="/sports" className="text-primary hover:underline">
          返回列表
        </Link>
      </div>
    );
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: "details", label: "比赛详情" },
    { id: "edge", label: "Edge 分析" },
    { id: "odds", label: "赔率对比" },
    { id: "realtime", label: "实时价格" },
  ];

  return (
    <div className="space-y-6">
      <Link href="/sports" className="text-sm text-muted-foreground hover:underline">
        ← 返回列表
      </Link>
      <div className="flex gap-2 border-b">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 ${
              activeTab === tab.id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab === "details" && (
        <>
          <MatchDetailPanel
            match={match}
            prediction={currentPrediction}
            onPredict={handlePredict}
            isPredicting={isPredicting}
            engines={engines}
            selectedEngine={selectedEngine}
            onEngineChange={setSelectedEngine}
          />
          {predictError && (
            <p className="text-sm text-destructive" role="alert">
              {predictError}
            </p>
          )}
          <div className="mt-6 space-y-4 border-t pt-4">
            <MatchRecommendationPanel matchId={matchId} />
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-muted-foreground">结算反馈：</span>
              <ProcessSettlementButton matchId={matchId} />
              <MatchSettlementPanel matchId={matchId} />
            </div>
          </div>
        </>
      )}
      {activeTab === "edge" && (
        <div className="space-y-6">
          <EdgeDetailPanel matchId={matchId} />
          <EdgeTimelineChart matchId={matchId} />
        </div>
      )}
      {activeTab === "odds" && <TraditionalOddsChart matchId={matchId} />}
      {activeTab === "realtime" && <RealtimePriceTable matchId={matchId} />}
    </div>
  );
}

/**
 * Match ids come from runtime fixtures, so they cannot be enumerated by
 * `generateStaticParams()` under `output: "export"`. The id travels as `?id=`
 * instead, which keeps this a single prerendered HTML file. `useSearchParams`
 * needs a Suspense boundary to prerender.
 */
export default function MatchDetailPage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-6 md:px-6">
      <Suspense fallback={<p className="text-muted-foreground">加载中...</p>}>
        <MatchDetailInner />
      </Suspense>
    </main>
  );
}

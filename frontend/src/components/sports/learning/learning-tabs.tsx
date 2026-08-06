"use client";

import { useCallback, useEffect, useReducer, useState } from "react";
import { EnginePerformancePanel } from "./engine-performance-panel";
import { PredictionHistoryList } from "./prediction-history-list";
import { CalibrationPanel } from "./calibration-panel";
import { AppliedWeightsPanel } from "./applied-weights-panel";

type TabId = "performance" | "history" | "calibration" | "weights";

const TABS: { id: TabId; label: string }[] = [
  { id: "performance", label: "性能对比" },
  { id: "history", label: "预测历史" },
  { id: "calibration", label: "校准诊断" },
  { id: "weights", label: "已应用权重" },
];

const VALID_TABS: TabId[] = ["performance", "history", "calibration", "weights"];

export function LearningTabs() {
  const [activeTab, setActiveTab] = useState<TabId>("performance");
  // refreshKey forces panel re-mount on refresh button click
  const [refreshKey, forceRefresh] = useReducer((x: number) => x + 1, 0);

  // Read ?tab= from URL on mount (spec 2.1: synced with URL query for shareability)
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab && VALID_TABS.includes(tab as TabId)) {
      // This page is prerendered by the static export, so the URL cannot be
      // read in a lazy useState initializer without a hydration mismatch
      // against HTML that always renders the default tab. Reading it after
      // mount is the correct trade here.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveTab(tab as TabId);
    }
  }, []);

  // Update URL on tab change (replaceState, no scroll jump)
  const handleTabChange = useCallback((tab: TabId) => {
    setActiveTab(tab);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (tab === "performance") {
      url.searchParams.delete("tab");
    } else {
      url.searchParams.set("tab", tab);
    }
    window.history.replaceState({}, "", url);
  }, []);

  const handleRefresh = useCallback(() => {
    forceRefresh();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">闭环学习仪表盘</h1>
        <button
          onClick={handleRefresh}
          className="rounded border border-border px-3 py-1 text-sm hover:bg-muted"
        >
          刷新
        </button>
      </div>
      <p
        data-testid="kernel-calibration-scope"
        className="rounded border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
      >
        此处为<strong className="font-medium text-foreground">体育 Kernel</strong>
        引擎表现与校准（NBA/足球等比赛预测）。事件情报（Polymarket 等）的
        事件层校准请看{" "}
        <a href="/history" className="text-primary underline">
          历史复盘
        </a>
        ；与市场结算对照请看{" "}
        <a href="/sports/settlements" className="text-primary underline">
          市场结算反馈
        </a>
        。
      </p>

      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-primary text-primary font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div key={`${activeTab}-${refreshKey}`}>
        {activeTab === "performance" && <EnginePerformancePanel />}
        {activeTab === "history" && <PredictionHistoryList />}
        {activeTab === "calibration" && <CalibrationPanel />}
        {activeTab === "weights" && <AppliedWeightsPanel />}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useReducer, useState } from "react";
import { EnginePerformancePanel } from "./engine-performance-panel";
import { PredictionHistoryList } from "./prediction-history-list";
import { CalibrationPanel } from "./calibration-panel";

type TabId = "performance" | "history" | "calibration";

const TABS: { id: TabId; label: string }[] = [
  { id: "performance", label: "性能对比" },
  { id: "history", label: "预测历史" },
  { id: "calibration", label: "校准诊断" },
];

const VALID_TABS: TabId[] = ["performance", "history", "calibration"];

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
      </div>
    </div>
  );
}

"use client";
import { useState } from "react";
import { MarketSnapshotBoard } from "@/components/sports/markets/market-snapshot-board";
import { MarketLinksTable } from "@/components/sports/markets/MarketLinksTable";
import { PendingReviewQueue } from "@/components/sports/markets/PendingReviewQueue";
import { FeatureDisabledBanner } from "@/components/sports/common/feature-disabled-banner";

type Tab = "snapshots" | "links" | "pending";

const TABS: { id: Tab; label: string }[] = [
  { id: "snapshots", label: "市场快照" },
  { id: "links", label: "链接列表" },
  { id: "pending", label: "待审核" },
];

export default function SportMarketsPage() {
  const [tab, setTab] = useState<Tab>("snapshots");

  return (
    <main id="main-content" className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育市场桥接</h1>
      <div className="mt-2">
        <FeatureDisabledBanner
          flag="PHASE7_SPORT_MARKET_BRIDGE_ENABLED"
          title="若列表 503"
          message="市场桥接需 PHASE7_SPORT_MARKET_BRIDGE_ENABLED=true。自动核验另需 PHASE7_SPORT_MARKET_LINK_AUTO_VERIFY_ENABLED。"
          testId="markets-flag-hint"
        />
      </div>

      <div
        role="tablist"
        aria-label="市场视图"
        className="mt-4 flex gap-1 border-b border-border"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`markets-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`markets-panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
              tab === t.id
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`markets-panel-${tab}`}
        aria-labelledby={`markets-tab-${tab}`}
        className="mt-4"
      >
        {tab === "snapshots" && <MarketSnapshotBoard />}
        {tab === "links" && <MarketLinksTable />}
        {tab === "pending" && <PendingReviewQueue />}
      </div>
    </main>
  );
}

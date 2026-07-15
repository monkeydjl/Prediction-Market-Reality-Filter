"use client";
import { useState } from "react";
import { MarketLinksTable } from "@/components/sports/markets/MarketLinksTable";
import { PendingReviewQueue } from "@/components/sports/markets/PendingReviewQueue";
import { MarketSnapshotChart } from "@/components/sports/markets/MarketSnapshotChart";

type Tab = "links" | "pending" | "snapshots";

export default function SportMarketsPage() {
  const [tab, setTab] = useState<Tab>("links");
  const [snapshotMatchId, setSnapshotMatchId] = useState("");

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育市场桥接</h1>
      <div className="mt-4 flex gap-2">
        <button
          onClick={() => setTab("links")}
          className={tab === "links" ? "bg-secondary" : ""}
        >
          链接列表
        </button>
        <button
          onClick={() => setTab("pending")}
          className={tab === "pending" ? "bg-secondary" : ""}
        >
          待审核
        </button>
        <button
          onClick={() => setTab("snapshots")}
          className={tab === "snapshots" ? "bg-secondary" : ""}
        >
          价格快照
        </button>
      </div>
      <div className="mt-4">
        {tab === "links" && <MarketLinksTable />}
        {tab === "pending" && <PendingReviewQueue />}
        {tab === "snapshots" && (
          <div>
            <input
              value={snapshotMatchId}
              onChange={(e) => setSnapshotMatchId(e.target.value)}
              placeholder="match_id"
              data-testid="match-input"
            />
            {snapshotMatchId && <MarketSnapshotChart matchId={snapshotMatchId} />}
          </div>
        )}
      </div>
    </main>
  );
}

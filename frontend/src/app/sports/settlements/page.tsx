"use client";
import { useState } from "react";
import { SettlementHistoryTable } from "@/components/sports/settlements/SettlementHistoryTable";
import { MarketCalibrationPanel } from "@/components/sports/settlements/MarketCalibrationPanel";

type Tab = "history" | "calibrations";

export default function SportSettlementsPage() {
  const [tab, setTab] = useState<Tab>("history");

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">市场结算反馈</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        赛后将市场结算与模型概率对照，独立于 Kernel 学习校准。需{" "}
        <code className="rounded bg-muted px-1">
          PHASE7_MARKET_SETTLEMENT_FEEDBACK_ENABLED
        </code>
        ；手动重算需要 write key。
      </p>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={() => setTab("history")}
          className={`rounded px-3 py-1 text-sm ${
            tab === "history" ? "bg-secondary" : "bg-muted"
          }`}
        >
          结算历史
        </button>
        <button
          type="button"
          onClick={() => setTab("calibrations")}
          className={`rounded px-3 py-1 text-sm ${
            tab === "calibrations" ? "bg-secondary" : "bg-muted"
          }`}
        >
          市场校准
        </button>
      </div>
      <div className="mt-4">
        {tab === "history" && <SettlementHistoryTable />}
        {tab === "calibrations" && <MarketCalibrationPanel />}
      </div>
    </main>
  );
}

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
      <div className="mt-4 flex gap-2">
        <button
          onClick={() => setTab("history")}
          className={tab === "history" ? "bg-secondary" : ""}
        >
          结算历史
        </button>
        <button
          onClick={() => setTab("calibrations")}
          className={tab === "calibrations" ? "bg-secondary" : ""}
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

"use client";

import { useState } from "react";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityOperationsDashboard } from "@/components/dashboard/quality-operations-dashboard";
import { QualityMetricsReportDashboard } from "@/components/dashboard/quality-metrics-report-dashboard";
import { QualityAlertsPanel } from "@/components/dashboard/quality-alerts-panel";
import { DomainReliabilityPanel } from "@/components/dashboard/domain-reliability-panel";

type Tab = "operations" | "slices";

const TABS: { id: Tab; label: string }[] = [
  { id: "operations", label: "运营总览" },
  { id: "slices", label: "质量切片" },
];

export default function QualityPage() {
  const [tab, setTab] = useState<Tab>("operations");

  return (
    <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
      <div
        role="tablist"
        aria-label="质量运营视图"
        className="mb-4 flex gap-1 border-b border-border"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`quality-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`quality-panel-${t.id}`}
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

      {tab === "operations" && (
        <div
          role="tabpanel"
          id="quality-panel-operations"
          aria-labelledby="quality-tab-operations"
          className="space-y-4"
        >
          <SectionErrorBoundary title="质量告警">
            <QualityAlertsPanel />
          </SectionErrorBoundary>
          <SectionErrorBoundary title="质量运营仪表盘">
            <QualityOperationsDashboard />
          </SectionErrorBoundary>
          <SectionErrorBoundary title="来源域名可靠性">
            <DomainReliabilityPanel />
          </SectionErrorBoundary>
        </div>
      )}

      {tab === "slices" && (
        <div
          role="tabpanel"
          id="quality-panel-slices"
          aria-labelledby="quality-tab-slices"
        >
          <SectionErrorBoundary title="质量切片报告">
            <QualityMetricsReportDashboard />
          </SectionErrorBoundary>
        </div>
      )}
    </main>
  );
}

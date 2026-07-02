"use client";

import { AppNav } from "@/components/app-nav";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityMetricsReportDashboard } from "@/components/dashboard/quality-metrics-report-dashboard";

export default function QualityMetricsReportPage() {
  return (
    <div className="min-h-screen bg-background">
      <AppNav />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary title="质量切片报告">
          <QualityMetricsReportDashboard />
        </SectionErrorBoundary>
      </main>
    </div>
  );
}

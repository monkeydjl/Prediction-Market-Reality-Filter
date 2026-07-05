"use client";

import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityMetricsReportDashboard } from "@/components/dashboard/quality-metrics-report-dashboard";

export default function QualityMetricsReportPage() {
  return (
      <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary title="质量切片报告">
          <QualityMetricsReportDashboard />
        </SectionErrorBoundary>
      </main>
  );
}

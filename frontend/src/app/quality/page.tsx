"use client";

import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityOperationsDashboard } from "@/components/dashboard/quality-operations-dashboard";

export default function QualityPage() {
  return (
      <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary title="质量运营仪表盘">
          <QualityOperationsDashboard />
        </SectionErrorBoundary>
      </main>
  );
}

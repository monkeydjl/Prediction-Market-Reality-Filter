"use client";

import { AppNav } from "@/components/app-nav";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { QualityOperationsDashboard } from "@/components/dashboard/quality-operations-dashboard";

export default function QualityPage() {
  return (
    <div className="min-h-screen bg-background">
      <AppNav />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary title="质量运营仪表盘">
          <QualityOperationsDashboard />
        </SectionErrorBoundary>
      </main>
    </div>
  );
}

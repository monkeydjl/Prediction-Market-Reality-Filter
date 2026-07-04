"use client";

import { AppNav } from "@/components/app-nav";
import { ReviewQueueWorkbench } from "@/components/review/review-queue-workbench";
import { SectionErrorBoundary } from "@/components/section-error-boundary";

export default function ReviewQueuePage() {
  return (
    <div className="min-h-screen bg-background">
      <AppNav />
      <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
        <SectionErrorBoundary title="复核队列">
          <ReviewQueueWorkbench />
        </SectionErrorBoundary>
      </main>
    </div>
  );
}

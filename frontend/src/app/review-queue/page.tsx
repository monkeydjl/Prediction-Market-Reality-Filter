"use client";

import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { ReviewQueueBoard } from "@/components/review/review-queue-board";

export default function ReviewQueuePage() {
  return (
    <main id="main-content" className="mx-auto max-w-6xl px-4 py-6">
      <h1 className="mb-1 text-lg font-semibold">人工复核</h1>
      <p className="mb-4 text-xs text-muted-foreground">
        探测器与编排器写入的复核候选。处理动作会追加到只增不改的审计日志，需要操作者写入密钥。
      </p>
      <SectionErrorBoundary title="复核队列">
        <ReviewQueueBoard />
      </SectionErrorBoundary>
    </main>
  );
}

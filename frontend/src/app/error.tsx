"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { AppNav } from "@/components/app-nav";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex min-h-[calc(100vh-3.5rem)] max-w-7xl items-center px-4 py-6 md:px-6 md:py-8">
        <section className="flex w-full flex-col items-center gap-4 rounded-lg border border-border bg-card px-6 py-12 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-neg/10 text-neg">
            <AlertTriangle className="size-6" aria-hidden="true" />
          </span>
          <div className="flex max-w-lg flex-col gap-2">
            <h1 className="text-xl font-semibold">页面加载失败</h1>
            <p className="text-sm leading-relaxed text-muted-foreground">
              页面在渲染过程中遇到了未处理错误。请重试；如果问题持续存在，再检查后端接口或最近变更。
            </p>
          </div>
          <button
            type="button"
            onClick={reset}
            className="inline-flex h-10 items-center gap-2 rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            重试
          </button>
        </section>
      </main>
    </div>
  );
}

"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function GlobalError({
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
    <html lang="zh">
      <body>
        <main className="flex min-h-screen items-center justify-center bg-background px-4 py-8 text-foreground">
          <section className="flex w-full max-w-lg flex-col items-center gap-4 rounded-lg border border-border bg-card px-6 py-12 text-center">
            <span className="flex size-12 items-center justify-center rounded-full bg-neg/10 text-neg">
              <AlertTriangle className="size-6" aria-hidden="true" />
            </span>
            <div className="flex flex-col gap-2">
              <h1 className="text-xl font-semibold">应用加载失败</h1>
              <p className="text-sm leading-relaxed text-muted-foreground">
                根布局渲染时遇到了未处理错误。请重试；如果问题持续存在，再检查最近部署或接口状态。
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
      </body>
    </html>
  );
}

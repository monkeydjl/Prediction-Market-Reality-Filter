"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Zap, Brain, Target, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface SwitchResult {
  status: string;
  message?: string;
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  engine?: string;
}

interface ProgressState {
  current: number;
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
}

interface BatchEngineSwitcherProps {
  onCompleted?: () => void | Promise<void>;
}

export function BatchEngineSwitcher({ onCompleted }: BatchEngineSwitcherProps) {
  const [switching, setSwitching] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressState | null>(null);
  const [lastResult, setLastResult] = useState<SwitchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => closeStream, [closeStream]);

  const handleSwitch = useCallback(
    (engine: string) => {
      if (switching) return;

      setSwitching(engine);
      setError(null);
      setLastResult(null);
      setProgress(null);

      // Use direct backend access for SSE stream (EventSource is GET-only,
      // bypasses Next.js rewrites which have ~30s timeout)
      const apiUrl =
        process.env.NODE_ENV === "development"
          ? process.env.NEXT_PUBLIC_API_ORIGIN || "http://localhost:8000"
          : "";
      const url = `${apiUrl}/api/world-cup/predictions/batch-switch-engine-stream?engine=${engine}&status_filter=scheduled`;

      const source = new EventSource(url);
      eventSourceRef.current = source;

      source.addEventListener("start", (e) => {
        try {
          const payload = JSON.parse(e.data) as { total: number };
          setProgress({
            current: 0,
            total: payload.total,
            succeeded: 0,
            failed: 0,
            skipped: 0,
          });
        } catch {
          // ignore malformed event
        }
      });

      source.addEventListener("progress", (e) => {
        try {
          const payload = JSON.parse(e.data) as ProgressState;
          setProgress(payload);
        } catch {
          // ignore malformed event
        }
      });

      source.addEventListener("complete", (e) => {
        try {
          const payload = JSON.parse(e.data) as SwitchResult;
          setLastResult(payload);
          if (payload.succeeded > 0) {
            void Promise.resolve(onCompleted?.()).catch((refreshError) => {
              console.error("Failed to refresh predictions after batch switch:", refreshError);
            });
          }
        } finally {
          closeStream();
          setSwitching(null);
        }
      });

      source.addEventListener("error", (e) => {
        // SSE 'error' event: could be a custom event payload or a transport error
        closeStream();
        setSwitching(null);
        // Custom error event from server
        if (e instanceof MessageEvent && typeof e.data === "string") {
          try {
            const payload = JSON.parse(e.data) as { message?: string };
            setError(payload.message || "切换失败");
            return;
          } catch {
            // fall through to generic error
          }
        }
        // Transport error (connection dropped)
        if (progress && progress.current > 0) {
          setError(`连接中断（已处理 ${progress.current}/${progress.total} 场）`);
        } else {
          setError("连接失败，请检查后端服务是否运行");
        }
      });
    },
    [switching, closeStream, onCompleted, progress],
  );

  const engines = [
    {
      id: "elo_odds",
      label: "一键 ELO",
      description: "快速 ELO + 赔率融合引擎",
      icon: Zap,
      accent: "text-amber-500",
      ring: "hover:border-amber-500/50",
      iconBg: "bg-amber-500/10",
    },
    {
      id: "hybrid",
      label: "一键混合引擎",
      description: "完整混合引擎（规则 + AI）",
      icon: Brain,
      accent: "text-purple-500",
      ring: "hover:border-purple-500/50",
      iconBg: "bg-purple-500/10",
    },
    {
      id: "high_confidence",
      label: "一键高置信度",
      description: "自动选择最佳引擎",
      icon: Target,
      accent: "text-emerald-500",
      ring: "hover:border-emerald-500/50",
      iconBg: "bg-emerald-500/10",
    },
  ];

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4">
        <h3 className="text-lg font-semibold">批量切换预测引擎</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          一键切换所有待定比赛的预测引擎（实时显示进度）
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {engines.map((engine) => {
          const Icon = engine.icon;
          const isSwitching = switching === engine.id;

          return (
            <button
              key={engine.id}
              onClick={() => handleSwitch(engine.id)}
              disabled={switching !== null}
              className={cn(
                "flex flex-col items-start gap-2 rounded-lg border border-border bg-secondary/30 p-4 text-left transition-all",
                engine.ring,
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                switching !== null && switching !== engine.id && "opacity-50 cursor-not-allowed"
              )}
            >
              <div className="flex w-full items-center justify-between">
                <div className={cn("rounded-md p-1.5", engine.iconBg)}>
                  <Icon className={cn("size-5", engine.accent)} />
                </div>
                {isSwitching && (
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                )}
              </div>
              <div className="flex-1">
                <div className="font-semibold text-foreground">{engine.label}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {engine.description}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Processing Status with Live Progress */}
      {switching && progress && (
        <div className="mt-4 rounded-lg border bg-secondary/50 p-4">
          <div className="flex items-start gap-3">
            <Loader2 className="size-5 shrink-0 animate-spin text-primary" />
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">
                正在批量切换引擎... {progress.current}/{progress.total}
              </p>
              <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{
                    width: progress.total > 0
                      ? `${(progress.current / progress.total) * 100}%`
                      : "0%",
                  }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="text-pos">成功: {progress.succeeded}</span>
                {progress.failed > 0 && (
                  <span className="text-neg">失败: {progress.failed}</span>
                )}
                {progress.skipped > 0 && (
                  <span className="text-warn">跳过: {progress.skipped}</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Processing Status (no progress yet, waiting for start event) */}
      {switching && !progress && (
        <div className="mt-4 rounded-lg border bg-secondary/50 p-4">
          <div className="flex items-start gap-3">
            <Loader2 className="size-5 shrink-0 animate-spin text-primary" />
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">正在连接后端...</p>
              <p className="mt-1 text-xs text-muted-foreground">
                正在初始化批量预测任务
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Result Display */}
      {lastResult && (
        <div className="mt-4 rounded-lg border bg-background p-4">
          <div className="flex items-start gap-3">
            {lastResult.succeeded > 0 ? (
              <CheckCircle2 className="size-5 shrink-0 text-pos" />
            ) : (
              <AlertCircle className="size-5 shrink-0 text-warn" />
            )}
            <div className="flex-1 space-y-1">
              <p className="text-sm font-medium text-foreground">
                {lastResult.message || "切换完成"}
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>总计: {lastResult.total}</span>
                <span className="text-pos">成功: {lastResult.succeeded}</span>
                {lastResult.failed > 0 && (
                  <span className="text-neg">失败: {lastResult.failed}</span>
                )}
                {lastResult.skipped > 0 && (
                  <span className="text-warn">跳过: {lastResult.skipped}</span>
                )}
              </div>
              {lastResult.succeeded > 0 && (
                <p className="text-xs text-muted-foreground">
                  预测列表正在后台刷新
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Error Display */}
      {error && (
        <div className="mt-4 rounded-lg border border-neg/40 bg-neg/10 p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="size-5 shrink-0 text-neg" />
            <div className="flex-1">
              <p className="text-sm font-medium text-neg">切换失败</p>
              <p className="mt-1 text-xs text-muted-foreground">{error}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

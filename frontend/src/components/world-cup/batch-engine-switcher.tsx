"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Zap, Brain, GitCompare, Target, Loader2, CheckCircle2, AlertCircle, BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getWorldCupApiBase } from "@/lib/env";
import { postHeaders } from "@/lib/world-cup/predictions-api";

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

interface SseEvent {
  event: string;
  data: string;
}

interface BatchEngineSwitcherProps {
  onCompleted?: () => void | Promise<void>;
}

function parseSseBlock(block: string): SseEvent | null {
  const lines = block.split(/\r?\n/);
  let event = "message";
  const data: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice("data:".length).trimStart());
    }
  }

  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}

export function BatchEngineSwitcher({ onCompleted }: BatchEngineSwitcherProps) {
  const [switching, setSwitching] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressState | null>(null);
  const [lastResult, setLastResult] = useState<SwitchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);

  const closeStream = useCallback(() => {
    if (streamControllerRef.current) {
      streamControllerRef.current.abort();
      streamControllerRef.current = null;
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

      // Use fetch streaming instead of EventSource so the protected endpoint can
      // receive the same operator API key header as other write requests.
      const apiUrl = getWorldCupApiBase();
      const url = `${apiUrl}/api/world-cup/predictions/batch-switch-engine-stream?engine=${engine}&status_filter=scheduled`;

      const controller = new AbortController();
      streamControllerRef.current = controller;

      void (async () => {
        try {
          const response = await fetch(url, {
            headers: postHeaders(),
            cache: "no-store",
            signal: controller.signal,
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          if (!response.body) {
            throw new Error("Stream body unavailable");
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          async function handleEvent(event: SseEvent): Promise<boolean> {
            if (event.event === "start") {
              const payload = JSON.parse(event.data) as { total: number };
              setProgress({
                current: 0,
                total: payload.total,
                succeeded: 0,
                failed: 0,
                skipped: 0,
              });
              return false;
            }

            if (event.event === "progress") {
              const payload = JSON.parse(event.data) as ProgressState;
              setProgress(payload);
              return false;
            }

            if (event.event === "complete") {
              const payload = JSON.parse(event.data) as SwitchResult;
              setLastResult(payload);
              if (payload.succeeded > 0) {
                await Promise.resolve(onCompleted?.()).catch((refreshError) => {
                  console.error("Failed to refresh predictions after batch switch:", refreshError);
                });
              }
              return true;
            }

            if (event.event === "error") {
              const payload = JSON.parse(event.data) as { message?: string };
              throw new Error(payload.message || "切换失败");
            }

            return false;
          }

          while (true) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value, { stream: !done });

            let separatorIndex = buffer.indexOf("\n\n");
            while (separatorIndex >= 0) {
              const rawEvent = buffer.slice(0, separatorIndex);
              buffer = buffer.slice(separatorIndex + 2);
              const event = parseSseBlock(rawEvent);
              if (event && await handleEvent(event)) {
                await reader.cancel();
                return;
              }
              separatorIndex = buffer.indexOf("\n\n");
            }

            if (done) break;
          }
        } catch (err) {
          if (controller.signal.aborted) return;
          const message = err instanceof Error ? err.message : String(err);
          setError(message.startsWith("HTTP 401") ? "当前请求未获授权" : message);
        } finally {
          if (streamControllerRef.current === controller) {
            streamControllerRef.current = null;
          }
          setSwitching(null);
        }
      })();
    },
    [switching, onCompleted],
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
      id: "integrated",
      label: "一键集成引擎",
      description: "融合 Elo+赔率 与混合引擎",
      icon: GitCompare,
      accent: "text-blue-500",
      ring: "hover:border-blue-500/50",
      iconBg: "bg-blue-500/10",
    },
    {
      id: "gbm",
      label: "一键 GBM",
      description: "梯度提升模型预测引擎",
      icon: BarChart3,
      accent: "text-teal-500",
      ring: "hover:border-teal-500/50",
      iconBg: "bg-teal-500/10",
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

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {engines.map((engine) => {
          const Icon = engine.icon;
          const isSwitching = switching === engine.id;

          return (
            <button
              key={engine.id}
              onClick={() => handleSwitch(engine.id)}
              disabled={switching !== null}
              title={`批量切换所有待定比赛的预测引擎为 ${engine.label}（写入操作，可能耗时数分钟）`}
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

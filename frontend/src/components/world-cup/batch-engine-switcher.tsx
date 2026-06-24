"use client";

import { useState } from "react";
import { Zap, Brain, Target, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { getOperatorApiKey } from "@/lib/api";

interface SwitchResult {
  status: string;
  message?: string;
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  engine?: string;
}

export function BatchEngineSwitcher() {
  const [switching, setSwitching] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SwitchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSwitch = async (engine: string) => {
    setSwitching(engine);
    setError(null);
    setLastResult(null);

    try {
      // Create an AbortController with 5 minutes timeout (hybrid engine calls AI per match, ~8s each)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 minutes

      // Use direct backend access for long-running batch operations
      // Next.js rewrites have timeout limitations for POST requests (~30s)
      // Hybrid engine needs ~3min for 24 matches (AI call per match)
      const apiUrl = process.env.NODE_ENV === "development"
        ? (process.env.NEXT_PUBLIC_API_ORIGIN || "http://localhost:8000")
        : "";

      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const key = getOperatorApiKey();
      if (key) headers["X-API-Key"] = key;

      const response = await fetch(
        `${apiUrl}/api/world-cup/predictions/batch-switch-engine?engine=${engine}&status_filter=scheduled`,
        {
          method: "POST",
          headers,
          signal: controller.signal,
        }
      );

      clearTimeout(timeoutId);

      if (!response.ok) {
        let errorText = await response.text();
        try {
          const errorJson = JSON.parse(errorText);
          errorText = errorJson.detail || errorJson.message || errorText;
        } catch {
          // errorText is already plain text
        }
        throw new Error(`HTTP ${response.status}: ${errorText}`);
      }

      const data = await response.json();
      setLastResult(data);

      // Refresh the page after 2 seconds to show updated predictions
      if (data.succeeded > 0) {
        setTimeout(() => {
          window.location.reload();
        }, 2000);
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setError("切换超时（超过5分钟），请稍后重试");
      } else {
        setError(err instanceof Error ? err.message : "切换失败");
      }
    } finally {
      setSwitching(null);
    }
  };

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
          一键切换所有待定比赛的预测引擎（预计需要30秒-2分钟）
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

      {/* Processing Status */}
      {switching && (
        <div className="mt-4 rounded-lg border bg-secondary/50 p-4">
          <div className="flex items-start gap-3">
            <Loader2 className="size-5 shrink-0 animate-spin text-primary" />
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">正在批量切换引擎...</p>
              <p className="mt-1 text-xs text-muted-foreground">
                预计需要30秒-2分钟，请耐心等待，不要关闭页面
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
                  页面将在 2 秒后自动刷新...
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

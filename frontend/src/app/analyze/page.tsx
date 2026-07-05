"use client";

import { useState } from "react";
import Link from "next/link";
import { FlaskConical } from "lucide-react";
import { SignalSummary } from "@/components/detail/signal-summary";
import { SignalPanel } from "@/components/detail/signal-panel";
import { DeltaPill } from "@/components/indicators";
import { eventsApi } from "@/lib/api";
import { adaptRecord } from "@/lib/adapt";
import { fmtPct } from "@/lib/format";
import type { EventRecord } from "@/lib/types";

const inputCls =
  "h-10 rounded-md border border-border bg-secondary px-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring";

export default function AnalyzePage() {
  const [question, setQuestion] = useState("");
  const [baseline, setBaseline] = useState("50");
  const [volume, setVolume] = useState("");
  const [liquidity, setLiquidity] = useState("");
  const [news, setNews] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EventRecord | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (baseline.trim() === "") {
      setError("请输入有效的基准概率");
      return;
    }
    const baselineValue = Number(baseline);
    if (!Number.isFinite(baselineValue)) {
      setError("请输入有效的基准概率");
      return;
    }
    if (baselineValue < 0 || baselineValue > 100) {
      setError("基准概率必须在 0 到 100 之间");
      return;
    }
    const volumeValue = volume.trim() === "" ? undefined : Number(volume);
    if (volumeValue !== undefined && (!Number.isFinite(volumeValue) || volumeValue < 0)) {
      setError("成交量必须是非负数字");
      return;
    }
    const liquidityValue = liquidity.trim() === "" ? undefined : Number(liquidity);
    if (liquidityValue !== undefined && (!Number.isFinite(liquidityValue) || liquidityValue < 0)) {
      setError("流动性必须是非负数字");
      return;
    }

    setPending(true);
    setError(null);
    setResult(null);
    try {
      const rec = await eventsApi.analyze({
        event_question: question.trim(),
        baseline_probability: baselineValue,
        news_context: news.trim() || undefined,
        volume: volumeValue,
        liquidity: liquidityValue,
      });
      setResult(rec);
    } catch (err) {
      setError(err instanceof Error ? err.message : "分析失败");
    } finally {
      setPending(false);
    }
  }

  const view = result ? adaptRecord(result) : null;

  return (
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-balance text-xl font-semibold md:text-2xl">人工分析</h1>
          <p className="text-sm text-muted-foreground">
            输入一个未来事件问题与基准概率，系统会收集证据、评估可信度并估计概率变化。
          </p>
        </div>

        <form
          onSubmit={submit}
          className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5"
        >
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">事件问题</label>
            <input
              className={inputCls}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：2026 年底前美联储是否会降息？"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5 sm:max-w-[200px]">
            <label className="text-xs text-muted-foreground">基准概率（0–100）</label>
            <input
              className={inputCls}
              type="number"
              min={0}
              max={100}
              step="any"
              value={baseline}
              onChange={(e) => setBaseline(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">成交量（可选）</label>
              <input
                className={inputCls}
                type="number"
                min={0}
                step="any"
                value={volume}
                onChange={(e) => setVolume(e.target.value)}
                placeholder="用于衡量市场深度"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">流动性（可选）</label>
              <input
                className={inputCls}
                type="number"
                min={0}
                step="any"
                value={liquidity}
                onChange={(e) => setLiquidity(e.target.value)}
                placeholder="用于 priced-in 风险评分"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-muted-foreground">新闻背景（可选）</label>
            <textarea
              className={`${inputCls} h-24 resize-y py-2`}
              value={news}
              onChange={(e) => setNews(e.target.value)}
              placeholder="粘贴相关新闻、官方声明或背景信息…"
            />
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={pending || !question.trim()}
              className="inline-flex h-10 items-center gap-2 rounded-md border border-primary bg-primary/15 px-4 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
            >
              <FlaskConical className={`size-4 ${pending ? "animate-pulse" : ""}`} aria-hidden="true" />
              {pending ? "分析中…" : "开始分析"}
            </button>
            {error && <span className="text-sm text-neg">{error}</span>}
          </div>
        </form>

        {view && result && (
          <>
            <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5">
              <h2 className="text-balance text-lg font-semibold">{view.title}</h2>
              {view.description && (
                <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                  {view.description}
                </p>
              )}
              <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
                <div className="flex flex-col">
                  <span className="text-xs text-muted-foreground">估计发生概率</span>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-4xl font-semibold tabular-nums">
                      {fmtPct(view.currentProbability)}
                    </span>
                    <DeltaPill delta={view.delta} />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">基准概率</span>
                  <span className="font-mono text-lg tabular-nums text-muted-foreground">
                    {fmtPct(view.baselineProbability)}
                  </span>
                </div>
              </div>
              <Link
                href={`/events?id=${encodeURIComponent(view.id)}`}
                className="w-fit text-sm text-primary hover:underline"
              >
                查看完整详情 →
              </Link>
            </div>

            <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
              <section className="flex flex-col gap-3">
                <h2 className="text-sm font-semibold">证据与交叉验证</h2>
                <SignalPanel record={result} />
              </section>
              <SignalSummary
                event={view}
                crossValidation={result.cross_validation}
                recommendedAction={result.intelligence_report?.recommended_action}
              />
            </div>
          </>
        )}
      </main>
  );
}

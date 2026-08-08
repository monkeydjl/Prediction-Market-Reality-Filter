"use client";

import { useState } from "react";
import { useEdgeLatest, detectEdges } from "@/lib/sports-api";
import type { EdgeResult, EdgeSource } from "@/lib/sports-api";

interface EdgeDetailPanelProps {
  matchId: string;
}

function formatPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatSignedPct(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function SourceRow({ source }: { source: EdgeSource }) {
  return (
    <li data-testid={`source-${source.link_id}`} className="text-xs">
      <span className="font-medium">{source.source}</span>
      <span className="text-muted-foreground"> · 合约 {source.contract_id}</span>
      <span className="text-muted-foreground"> · 隐含 {formatPct(source.implied_prob)}</span>
      <span className="text-muted-foreground"> · 权重 {source.weight.toFixed(2)}</span>
      <span className="text-muted-foreground"> · 置信 {formatPct(source.link_confidence)}</span>
      {source.liquidity !== null && (
        <span className="text-muted-foreground"> · 流动性 {source.liquidity}</span>
      )}
      {source.volume !== null && (
        <span className="text-muted-foreground"> · 成交 {source.volume}</span>
      )}
    </li>
  );
}

const PRIORITY_ZH: Record<string, string> = {
  critical: "紧急审查",
  high: "优先审查",
  normal: "普通",
  low: "低优先级",
};


function disagreementDiagnosis(outcome: EdgeResult): string | null {
  const gap = outcome.model_prob - outcome.market_prob;
  const absGap = Math.abs(gap);
  const priority = outcome.review_priority ?? "normal";
  if (absGap < 0.03 && priority !== "critical" && priority !== "high") {
    return null;
  }
  const lean = gap > 0 ? "模型偏高" : "市场偏高";
  const attr = outcome.factor_attribution ? ` ${outcome.factor_attribution}` : "";
  if (outcome.stale) {
    return `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。数据过期，优先怀疑市场快照时效。${attr}`;
  }
  if (outcome.liquidity_factor < 0.25) {
    return `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。流动性差，优先怀疑市场噪音。${attr}`;
  }
  if (outcome.trust < 0.35) {
    return `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。模型信任度低，优先怀疑模型。${attr}`;
  }
  if (absGap >= 0.12 && outcome.trust >= 0.6 && outcome.liquidity_factor >= 0.4) {
    return gap > 0
      ? `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。大分歧且信任/流动性尚可：市场可能滞后。${attr}`
      : `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。大分歧：市场或已定价模型未覆盖信息。${attr}`;
  }
  return `分歧诊断：${lean}（Δ=${(gap * 100).toFixed(1)}pp）。建议对照因子分解与多源赔率。${attr}`;
}


function OutcomeCard({ outcome }: { outcome: EdgeResult }) {
  const priority = outcome.review_priority ?? "normal";
  return (
    <div
      data-testid={`outcome-${outcome.mapped_outcome}`}
      className="rounded-lg border border-border p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">{outcome.mapped_outcome}</span>
        <div className="flex items-center gap-1.5">
          {priority !== "normal" && (
            <span
              data-testid={`priority-${outcome.mapped_outcome}`}
              className={`rounded px-2 py-0.5 text-xs ${
                priority === "critical"
                  ? "bg-neg/15 text-neg"
                  : priority === "high"
                    ? "bg-amber-500/15 text-amber-400"
                    : "bg-muted text-muted-foreground"
              }`}
            >
              {PRIORITY_ZH[priority] ?? priority}
            </span>
          )}
          {outcome.stale ? (
            <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              过期
            </span>
          ) : (
            <span className="rounded bg-primary/15 px-2 py-0.5 text-xs text-primary">
              活跃
            </span>
          )}
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">模型概率</dt>
          <dd data-testid={`model-prob-${outcome.mapped_outcome}`}>
            {formatPct(outcome.model_prob)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">市场概率</dt>
          <dd data-testid={`market-prob-${outcome.mapped_outcome}`}>
            {formatPct(outcome.market_prob)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">调整 Edge</dt>
          <dd data-testid={`adjusted-edge-${outcome.mapped_outcome}`}>
            {formatSignedPct(outcome.adjusted_edge)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">原始 Edge</dt>
          <dd>{formatSignedPct(outcome.raw_edge)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">信任度</dt>
          <dd data-testid={`trust-${outcome.mapped_outcome}`}>
            {formatPct(outcome.trust)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">流动性因子</dt>
          <dd data-testid={`liquidity-factor-${outcome.mapped_outcome}`}>
            {outcome.liquidity_factor.toFixed(2)}
          </dd>
        </div>
        {outcome.spread !== null && (
          <div>
            <dt className="text-xs text-muted-foreground">价差</dt>
            <dd>{formatPct(outcome.spread)}</dd>
          </div>
        )}
        <div>
          <dt className="text-xs text-muted-foreground">来源数量</dt>
          <dd>{outcome.sources_count}</dd>
        </div>
      </dl>
      {disagreementDiagnosis(outcome) && (
        <p
          data-testid={`disagreement-diagnosis-${outcome.mapped_outcome}`}
          className="mt-3 rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-xs text-muted-foreground"
        >
          {disagreementDiagnosis(outcome)}
        </p>
      )}
      {outcome.factor_drivers && outcome.factor_drivers.length > 0 && (
        <ul
          data-testid={`factor-drivers-${outcome.mapped_outcome}`}
          className="mt-2 flex flex-wrap gap-1.5 text-[11px]"
        >
          {outcome.factor_drivers.map((d) => (
            <li
              key={d.factor}
              className="rounded bg-secondary px-1.5 py-0.5 font-mono text-muted-foreground"
            >
              {d.factor} {d.impact >= 0 ? "+" : ""}
              {d.impact.toFixed(3)}
            </li>
          ))}
        </ul>
      )}
      {outcome.sources.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-muted-foreground">来源详情</p>
          <ul className="mt-1 space-y-1">
            {outcome.sources.map((source) => (
              <SourceRow key={source.link_id} source={source} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DetectButton({
  busy,
  onClick,
  label = "重新计算 Edge",
}: {
  busy: boolean;
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      data-testid="detect-edges-button"
      onClick={onClick}
      disabled={busy}
      className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
    >
      {busy ? "计算中..." : label}
    </button>
  );
}

export function EdgeDetailPanel({ matchId }: EdgeDetailPanelProps) {
  const { data, error, isLoading, mutate } = useEdgeLatest(matchId);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  const handleDetect = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await detectEdges(matchId);
      await mutate();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "重新计算失败");
    } finally {
      setBusy(false);
    }
  };

  if (isLoading) return <div data-testid="loading">加载中...</div>;

  if (errorMessage) {
    return (
      <div className="space-y-2">
        <div data-testid="error">{errorMessage}</div>
        <DetectButton busy={busy} onClick={handleDetect} />
        {actionError && (
          <p className="text-sm text-destructive" role="alert">
            {actionError}
          </p>
        )}
      </div>
    );
  }

  if (!data) return <div data-testid="empty">暂无 Edge 详情</div>;

  if (data.skipped) {
    return (
      <div
        data-testid="skipped"
        className="space-y-3 rounded-lg border border-border p-4"
      >
        <div>
          <p className="font-semibold">Edge 计算已跳过</p>
          <p className="mt-1 text-sm text-muted-foreground">
            跳过原因:{" "}
            <span data-testid="skip-reason">{data.skip_reason ?? "未知"}</span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            需已有预测与已验证市场链接。可点击下方按钮即时重算。
          </p>
        </div>
        <DetectButton busy={busy} onClick={handleDetect} />
        {actionError && (
          <p className="text-sm text-destructive" role="alert">
            {actionError}
          </p>
        )}
      </div>
    );
  }

  return (
    <div data-testid="edge-detail-panel" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          {data.engine_name && <span>引擎: {data.engine_name}</span>}
          {data.competition && <span>赛事: {data.competition}</span>}
          {data.prediction_timestamp && (
            <span>预测时间: {data.prediction_timestamp}</span>
          )}
        </div>
        <DetectButton busy={busy} onClick={handleDetect} label="重新计算" />
      </div>
      {actionError && (
        <p className="text-sm text-destructive" role="alert">
          {actionError}
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {data.outcomes.map((outcome) => (
          <OutcomeCard key={outcome.mapped_outcome} outcome={outcome} />
        ))}
      </div>
    </div>
  );
}

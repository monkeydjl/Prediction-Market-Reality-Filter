"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CONFIGURED_SOURCES,
  PAYLOAD_SOURCES,
  ingestApi,
  parseIngestPayload,
  type FactsResponse,
  type IngestResult,
  type SourceAction,
} from "@/lib/world-cup/ingest-api";

const BTN =
  "rounded-md px-2.5 py-1 text-xs font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50";
const BTN_OUTLINE = `${BTN} border border-border text-foreground hover:bg-muted`;
const FIELD =
  "rounded-md border border-input bg-card px-2 py-1.5 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

const ACTION_LABELS: Record<SourceAction, string> = {
  preview: "预览",
  import: "导入",
  test: "连通性测试",
  validate: "流水线校验",
};

/** A destructive import needs the operator to confirm once more (OQ-5).
 *
 * ``execute`` closes over the flags as they were when the dialog opened, so the
 * inputs that feed it (替换模式, 试运行) are disabled while it is open. Without
 * that, unchecking 替换模式 mid-dialog left the warning gone from the screen but
 * the destructive replace still queued in the closure.
 */
type PendingConfirm = {
  label: string;
  detail: string;
  execute: () => void;
};

function summarize(result: IngestResult): string {
  const parts: string[] = [];
  if (typeof result.converted_fact_count === "number") {
    parts.push(`可转换 ${result.converted_fact_count} 条事实`);
  }
  if (typeof result.imported === "number") parts.push(`已导入 ${result.imported}`);
  if (typeof result.replaced === "number") parts.push(`已替换 ${result.replaced}`);
  if (typeof result.skipped === "number") parts.push(`跳过 ${result.skipped}`);
  if (typeof result.error_count === "number") parts.push(`错误 ${result.error_count}`);
  if (typeof result.count === "number" && parts.length === 0) parts.push(`共 ${result.count} 条`);
  if (typeof result.ok === "boolean") parts.push(result.ok ? "校验通过" : "校验未通过");
  if (parts.length === 0 && result.status) parts.push(String(result.status));
  const head = parts.join(" / ");
  return result.message ? (head ? `${head} · ${result.message}` : String(result.message)) : head;
}

function ResultBlock({ testId, result }: { testId: string; result: IngestResult }) {
  const line = summarize(result);
  return (
    <div data-testid={testId} className="mt-3 space-y-2 text-sm">
      {line && <p className="text-foreground">{line}</p>}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          查看原始响应
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </div>
  );
}

/**
 * Operator console for World Cup data ingestion: configured provider feeds,
 * pasted JSON payloads, the fact store, and event resolution.
 *
 * Writes are guarded by the operator API key on the backend; imports also take
 * a second in-app confirmation because they rewrite the shared fact store.
 */
export function IngestConsole() {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);

  const [factStatus, setFactStatus] = useState<IngestResult | null>(null);
  const [sourceStatus, setSourceStatus] = useState<IngestResult | null>(null);
  const [sourceResult, setSourceResult] = useState<IngestResult | null>(null);
  const [payloadResult, setPayloadResult] = useState<IngestResult | null>(null);
  const [resolveResult, setResolveResult] = useState<IngestResult | null>(null);

  const [replace, setReplace] = useState(false);
  const [payloadKey, setPayloadKey] = useState(PAYLOAD_SOURCES[0].key);
  const [payloadText, setPayloadText] = useState("");

  const [factKind, setFactKind] = useState("");
  const [factTeam, setFactTeam] = useState("");
  const [facts, setFacts] = useState<FactsResponse | null>(null);

  const [dryRun, setDryRun] = useState(true);
  const [resolveLimit, setResolveLimit] = useState(200);

  const run = useCallback(
    async <T,>(key: string, op: () => Promise<T>, onDone: (result: T) => void) => {
      setPending(key);
      setError(null);
      try {
        onDone(await op());
      } catch (e) {
        setError(e instanceof Error ? e.message : "操作失败");
      } finally {
        setPending(null);
      }
    },
    [],
  );

  // The fact-store status is the only unauthenticated read here, so it is the
  // one thing safe to load without the operator having entered a key.
  useEffect(() => {
    let cancelled = false;
    const timer = globalThis.setTimeout(() => {
      void ingestApi
        .status()
        .then((result) => {
          if (!cancelled) setFactStatus(result);
        })
        .catch(() => {
          // Status is informational; a failure here must not block the console.
        });
    }, 0);
    return () => {
      cancelled = true;
      globalThis.clearTimeout(timer);
    };
  }, []);

  const busy = pending !== null;
  const payloadSource =
    PAYLOAD_SOURCES.find((s) => s.key === payloadKey) ?? PAYLOAD_SOURCES[0];

  const runSourceAction = useCallback(
    (path: string, label: string, action: SourceAction) => {
      const key = `${path}:${action}`;
      const execute = () =>
        void run(key, () => ingestApi.runConfigured(path, action, replace), setSourceResult);
      if (action !== "import") {
        execute();
        return;
      }
      setConfirm({
        label: `确认从「${label}」导入`,
        detail: replace
          ? "替换模式会先清空该来源的现有事实，再写入新数据，且不可撤销。"
          : "导入会把该来源的数据写入共享事实库。",
        execute: () => {
          setConfirm(null);
          execute();
        },
      });
    },
    [replace, run],
  );

  const runPayloadAction = useCallback(
    (action: "preview" | "import") => {
      let payload: unknown;
      try {
        payload = parseIngestPayload(payloadText);
      } catch (e) {
        setError(e instanceof Error ? e.message : "载荷无效");
        return;
      }
      const key = `payload:${action}`;
      const execute = () =>
        void run(
          key,
          () => ingestApi.runPayload(payloadSource.path, action, payload, replace),
          setPayloadResult,
        );
      if (action === "preview") {
        execute();
        return;
      }
      setConfirm({
        label: `确认导入「${payloadSource.label}」载荷`,
        detail: replace
          ? "替换模式会先清空对应类别的现有事实，再写入新数据，且不可撤销。"
          : "导入会把粘贴的载荷写入共享事实库。",
        execute: () => {
          setConfirm(null);
          execute();
        },
      });
    },
    [payloadSource, payloadText, replace, run],
  );

  return (
    <section data-testid="ingest-console" className="space-y-6">
      {error && (
        <p
          data-testid="ingest-error"
          role="alert"
          className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-sm text-neg"
        >
          {error}
        </p>
      )}

      {confirm && (
        <div
          data-testid="ingest-confirm"
          role="alertdialog"
          aria-label="确认导入操作"
          className="rounded-md border border-warn/40 bg-warn/10 p-3"
        >
          <p className="text-sm font-medium text-foreground">{confirm.label}</p>
          <p className="mt-1 text-xs text-muted-foreground">{confirm.detail}</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              data-testid="ingest-confirm-yes"
              onClick={confirm.execute}
              className={`${BTN} bg-neg text-neg-foreground hover:opacity-90`}
            >
              确认执行
            </button>
            <button
              type="button"
              data-testid="ingest-confirm-no"
              onClick={() => setConfirm(null)}
              className={BTN_OUTLINE}
            >
              取消
            </button>
          </div>
        </div>
      )}

      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">运行状态</h3>
          <button
            type="button"
            data-testid="source-status-button"
            disabled={busy}
            onClick={() => void run("source-status", ingestApi.sourceStatus, setSourceStatus)}
            className={BTN_OUTLINE}
          >
            {pending === "source-status" ? "读取中…" : "读取数据源状态"}
          </button>
        </div>
        {factStatus && <ResultBlock testId="fact-status-result" result={factStatus} />}
        {sourceStatus && <ResultBlock testId="source-status-result" result={sourceStatus} />}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">配置数据源</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              载荷来自后端配置（数据文件、Feed URL、供应商密钥），前端只触发预览与导入。
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              data-testid="replace-toggle"
              checked={replace}
              disabled={busy || confirm !== null}
              onChange={(e) => setReplace(e.target.checked)}
              className="size-3.5 accent-primary disabled:cursor-not-allowed disabled:opacity-50"
            />
            替换模式（先清空同类事实）
          </label>
        </div>

        <ul className="mt-3 divide-y divide-border">
          {CONFIGURED_SOURCES.map((source) => (
            <li
              key={source.key}
              data-testid={`source-row-${source.key}`}
              className="flex flex-wrap items-start justify-between gap-3 py-3"
            >
              <div className="min-w-0">
                <p className="text-sm text-foreground">{source.label}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{source.description}</p>
                {source.note && <p className="mt-0.5 text-xs text-warn">{source.note}</p>}
              </div>
              <div className="flex flex-wrap gap-2">
                {source.actions.map((action) => (
                  <button
                    key={action}
                    type="button"
                    data-testid={`source-${source.key}-${action}`}
                    disabled={busy}
                    onClick={() => runSourceAction(source.path, source.label, action)}
                    className={
                      action === "import"
                        ? `${BTN} bg-primary text-primary-foreground hover:opacity-90`
                        : BTN_OUTLINE
                    }
                  >
                    {pending === `${source.path}:${action}`
                      ? "执行中…"
                      : ACTION_LABELS[action]}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>

        {sourceResult && <ResultBlock testId="source-result" result={sourceResult} />}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">载荷导入</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          粘贴 JSON 载荷后先预览转换结果，确认无误再导入。
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            载荷类别
            <select
              data-testid="payload-kind"
              value={payloadKey}
              onChange={(e) => {
                setPayloadKey(e.target.value);
                setPayloadResult(null);
              }}
              className={FIELD}
            >
              {PAYLOAD_SOURCES.map((source) => (
                <option key={source.key} value={source.key}>
                  {source.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="payload-preview"
            disabled={busy || !payloadSource.supportsPreview}
            onClick={() => runPayloadAction("preview")}
            className={BTN_OUTLINE}
          >
            {pending === "payload:preview" ? "预览中…" : "预览"}
          </button>
          <button
            type="button"
            data-testid="payload-import"
            disabled={busy}
            onClick={() => runPayloadAction("import")}
            className={`${BTN} bg-primary text-primary-foreground hover:opacity-90`}
          >
            {pending === "payload:import" ? "导入中…" : "导入"}
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {payloadSource.description}
          {!payloadSource.supportsPreview && " 该类别仅支持直接导入。"}
        </p>
        <textarea
          data-testid="payload-input"
          value={payloadText}
          onChange={(e) => setPayloadText(e.target.value)}
          rows={8}
          spellCheck={false}
          placeholder='{"matches": []}'
          aria-label="JSON 载荷"
          className={`${FIELD} mt-3 w-full font-mono text-xs`}
        />
        {payloadResult && <ResultBlock testId="payload-result" result={payloadResult} />}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">事实库查询</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            事实类别
            <input
              data-testid="fact-kind"
              value={factKind}
              onChange={(e) => setFactKind(e.target.value)}
              placeholder="如 match_result"
              className={`${FIELD} w-44`}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            球队
            <input
              data-testid="fact-team"
              value={factTeam}
              onChange={(e) => setFactTeam(e.target.value)}
              placeholder="如 Brazil"
              className={`${FIELD} w-44`}
            />
          </label>
          <button
            type="button"
            data-testid="facts-button"
            disabled={busy}
            onClick={() =>
              void run(
                "facts",
                () => ingestApi.facts({ kind: factKind.trim(), team: factTeam.trim() }),
                setFacts,
              )
            }
            className={BTN_OUTLINE}
          >
            {pending === "facts" ? "查询中…" : "查询事实"}
          </button>
        </div>
        {facts && (
          <div data-testid="facts-result" className="mt-3 text-sm">
            <p className="text-foreground">命中 {facts.count ?? facts.facts?.length ?? 0} 条事实</p>
            {(facts.facts?.length ?? 0) === 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                该筛选条件下暂无事实，请调整类别或球队后重试。
              </p>
            ) : (
              <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs text-foreground">
                {JSON.stringify(facts.facts?.slice(0, 20), null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold">赛事结算</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          依据已导入事实结算世界杯事件。默认试运行，仅列出将要写入的结果。
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              data-testid="resolve-dry-run"
              checked={dryRun}
              disabled={busy || confirm !== null}
              onChange={(e) => setDryRun(e.target.checked)}
              className="size-3.5 accent-primary disabled:cursor-not-allowed disabled:opacity-50"
            />
            试运行（不写入）
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            上限
            <input
              type="number"
              min={1}
              max={1000}
              data-testid="resolve-limit"
              value={resolveLimit}
              onChange={(e) => setResolveLimit(Number(e.target.value) || 1)}
              className={`${FIELD} w-24`}
            />
          </label>
          <button
            type="button"
            data-testid="resolve-button"
            disabled={busy}
            onClick={() => {
              const execute = () =>
                void run(
                  "resolve",
                  () => ingestApi.resolve(dryRun, resolveLimit),
                  setResolveResult,
                );
              if (dryRun) {
                execute();
                return;
              }
              setConfirm({
                label: "确认写入结算结果",
                detail: "关闭试运行后会直接写入事件结算结果，且不可撤销。",
                execute: () => {
                  setConfirm(null);
                  execute();
                },
              });
            }}
            className={
              dryRun ? BTN_OUTLINE : `${BTN} bg-neg text-neg-foreground hover:opacity-90`
            }
          >
            {pending === "resolve" ? "结算中…" : dryRun ? "试运行结算" : "正式结算"}
          </button>
        </div>
        {resolveResult && <ResultBlock testId="resolve-result" result={resolveResult} />}
      </div>
    </section>
  );
}

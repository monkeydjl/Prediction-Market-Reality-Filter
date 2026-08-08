/**
 * Client for the World Cup engine console (`/api/world-cup/predictions/*`
 * batch + tuning + calibration routes).
 *
 * Every route here is a write or a long-running analysis guarded by
 * `require_write_key` on the backend, so all calls go through the shared
 * operator auth headers and are bounded by an `AbortController` timeout.
 *
 * One backend constraint shapes this module: the SSE progress route
 * (`GET /batch-switch-engine-stream`) is *also* behind `require_write_key`,
 * which reads `X-API-Key` from a request header. The native `EventSource` API
 * cannot set request headers, so streaming is implemented with `fetch` +
 * `ReadableStream` and a small SSE frame parser rather than `EventSource`.
 */

import { getApiBase } from "@/lib/env";
import {
  ApiError,
  buildApiErrorMessage,
  buildOperatorAuthHeaders,
  handleFetchError,
} from "@/lib/api";

const API_BASE = getApiBase();
const ENGINE_CLIENT_SOURCE = "world-cup-engine-console";
const DEFAULT_TIMEOUT_MS = 60_000;
/** Batch routes walk every scheduled match through the pipeline. */
const BATCH_TIMEOUT_MS = 600_000;

export type EngineName = "elo_odds" | "hybrid" | "integrated" | "high_confidence";
export type TunableEngine = "elo_odds" | "hybrid" | "integrated";

export interface BatchSummary {
  status?: string;
  message?: string;
  total?: number;
  succeeded?: number;
  failed?: number;
  skipped?: number;
  elo_odds_count?: number;
  hybrid_count?: number;
  integrated_count?: number;
}

export interface BatchProgressEvent {
  current: number;
  total: number;
  match_id: string;
  status?: string;
  error?: string;
  succeeded: number;
  failed: number;
  skipped: number;
}

export interface AutoTuneAccepted {
  status?: string;
  task_id?: string;
  message?: string;
  poll_url?: string;
}

export interface AutoTuneTask {
  task_id?: string;
  engine?: string;
  status?: string;
  progress?: number;
  stage?: string;
  message?: string;
  error?: string;
  result?: Record<string, unknown> | null;
  created_at?: string;
  updated_at?: string;
}

export interface EngineCalibration {
  status?: string;
  engine?: string;
  message?: string;
  calibration?: Record<string, unknown> | null;
}

export interface CalibrationPatterns {
  status?: string;
  engine?: string;
  message?: string;
  sample_count?: number;
  patterns?: Record<string, unknown> | null;
  suggestions?: Record<string, unknown> | null;
}

function engineHeaders(): Record<string, string> {
  return {
    "X-Client-Source": ENGINE_CLIENT_SOURCE,
    ...buildOperatorAuthHeaders(),
  };
}

async function engineFetch<T>(
  path: string,
  options: { method?: "GET" | "POST"; timeoutMs?: number } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? "GET",
      headers: engineHeaders(),
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) {
      const bodyText = await res.text();
      throw new ApiError(res.status, buildApiErrorMessage(res.status, bodyText));
    }
    return (await res.json()) as T;
  } catch (error) {
    handleFetchError(error);
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

const PREFIX = "/world-cup/predictions";

export const engineApi = {
  batchPredict: (engine: EngineName | "auto"): Promise<BatchSummary> =>
    engineFetch<BatchSummary>(
      `${PREFIX}/batch-predict?engine=${encodeURIComponent(engine)}`,
      { method: "POST", timeoutMs: BATCH_TIMEOUT_MS },
    ),

  /**
   * Long-poll batch engine switch. Returns only the aggregate summary — prefer
   * `streamBatchSwitchEngine` when the caller can show per-match progress.
   */
  batchSwitchEngine: (engine: EngineName, statusFilter = "scheduled"): Promise<BatchSummary> =>
    engineFetch<BatchSummary>(
      `${PREFIX}/batch-switch-engine?engine=${encodeURIComponent(engine)}&status_filter=${encodeURIComponent(statusFilter)}`,
      { method: "POST", timeoutMs: BATCH_TIMEOUT_MS },
    ),

  batchOptimize: (engine: TunableEngine | "", limit: number): Promise<BatchSummary> => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (engine) params.set("engine", engine);
    return engineFetch<BatchSummary>(`${PREFIX}/batch-optimize?${params}`, {
      method: "POST",
      timeoutMs: BATCH_TIMEOUT_MS,
    });
  },

  autoTune: (engine: TunableEngine): Promise<AutoTuneAccepted> =>
    engineFetch<AutoTuneAccepted>(
      `${PREFIX}/auto-tune/${encodeURIComponent(engine)}?background=true`,
      { method: "POST" },
    ),

  autoTuneStatus: (taskId: string): Promise<{ status?: string; task?: AutoTuneTask }> =>
    engineFetch<{ status?: string; task?: AutoTuneTask }>(
      `${PREFIX}/auto-tune/status/${encodeURIComponent(taskId)}`,
    ),

  calibration: (engine: TunableEngine): Promise<EngineCalibration> =>
    engineFetch<EngineCalibration>(`${PREFIX}/calibration/${encodeURIComponent(engine)}`),

  calibrationPatterns: (engine: TunableEngine): Promise<CalibrationPatterns> =>
    engineFetch<CalibrationPatterns>(
      `${PREFIX}/calibration-patterns/${encodeURIComponent(engine)}`,
    ),
};

export interface StreamBatchSwitchHandlers {
  onStart?: (payload: { total: number; engine: string }) => void;
  onProgress?: (payload: BatchProgressEvent) => void;
  onComplete?: (payload: BatchSummary) => void;
  onError?: (message: string) => void;
  signal?: AbortSignal;
}

/**
 * Parse one SSE frame ("event: <name>\ndata: <json>") into a name/payload pair.
 * Returns null for frames without a data line (comments, heartbeats).
 */
function parseSseFrame(frame: string): { event: string; data: unknown } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

/**
 * Stream batch engine-switch progress. Uses `fetch` + `ReadableStream` because
 * the route is auth-guarded by a request header that `EventSource` cannot set.
 */
export async function streamBatchSwitchEngine(
  engine: EngineName,
  statusFilter: string,
  handlers: StreamBatchSwitchHandlers,
): Promise<void> {
  const url =
    `${API_BASE}${PREFIX}/batch-switch-engine-stream` +
    `?engine=${encodeURIComponent(engine)}&status_filter=${encodeURIComponent(statusFilter)}`;

  let res: Response;
  try {
    res = await fetch(url, {
      headers: { ...engineHeaders(), Accept: "text/event-stream" },
      cache: "no-store",
      signal: handlers.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") return;
    handlers.onError?.(
      error instanceof TypeError
        ? "无法连接到服务器，请检查网络或后端服务状态"
        : error instanceof Error
          ? error.message
          : "批量切换失败",
    );
    return;
  }

  if (!res.ok) {
    const bodyText = await res.text().catch(() => "");
    handlers.onError?.(buildApiErrorMessage(res.status, bodyText));
    return;
  }
  if (!res.body) {
    handlers.onError?.("服务器未返回流式响应");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseFrame(frame);
        if (parsed) {
          if (parsed.event === "start") {
            handlers.onStart?.(parsed.data as { total: number; engine: string });
          } else if (parsed.event === "progress") {
            handlers.onProgress?.(parsed.data as BatchProgressEvent);
          } else if (parsed.event === "complete") {
            handlers.onComplete?.(parsed.data as BatchSummary);
          } else if (parsed.event === "error") {
            handlers.onError?.((parsed.data as { message?: string }).message ?? "批量切换失败");
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") return;
    handlers.onError?.(error instanceof Error ? error.message : "读取流式响应失败");
  } finally {
    reader.releaseLock();
  }
}

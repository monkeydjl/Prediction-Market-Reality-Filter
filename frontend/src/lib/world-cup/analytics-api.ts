/**
 * Unified API client for the World Cup analytics dashboard endpoints.
 *
 * Previously the dashboard reached the backend with bare `fetch()` calls — GETs
 * carried no auth headers, no timeout, and produced opaque `HTTP <status>`
 * errors. This client mirrors the central `api()` client in `./api.ts` but
 * targets the World Cup API base (origin without the `/api` prefix, so paths
 * keep their leading `/api/analytics/...`), so:
 *
 * - Every request (GET included) injects `X-API-Key` / `X-Operator` from the
 *   operator session — keeping the dashboard consistent with the rest of the
 *   authenticated surface and ready for the day GETs start requiring a key.
 * - Every request carries `X-Client-Source: world-cup-dashboard` so backend
 *   audit logs can attribute dashboard-triggered writes.
 * - Every request is bounded by an `AbortController` timeout (60s default,
 *   180s for the long backfill operations that hit LLM/odds providers).
 * - Failures surface as localized, user-readable messages via
 *   `buildApiErrorMessage` instead of `HTTP 500`.
 *
 * The dashboard's existing local interfaces (`EngineStats`, etc.) stay where
 * they are; callers pass the expected return type as the generic `T`.
 */

import { getApiBase } from "@/lib/env";
import {
  ApiError,
  buildApiErrorMessage,
  buildOperatorAuthHeaders,
  handleFetchError,
} from "@/lib/api";

const API_BASE = getApiBase();
const ANALYTICS_CLIENT_SOURCE = "world-cup-dashboard";
const DEFAULT_TIMEOUT_MS = 60_000;
const LONG_OPERATION_TIMEOUT_MS = 180_000;

/**
 * Build the standard analytics request headers (operator key + operator id +
 * client source). Mirrors `postHeaders()` from `world-cup-predictions.ts` but
 * adds the `X-Client-Source` tag so backend audit metadata can attribute
 * dashboard-triggered writes ("via world-cup-dashboard / <operator>").
 */
export function analyticsHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {
    "X-Client-Source": ANALYTICS_CLIENT_SOURCE,
    ...buildOperatorAuthHeaders(),
  };
  return extra ? { ...headers, ...Object.fromEntries(new Headers(extra)) } : headers;
}

export interface AnalyticsFetchOptions {
  method?: "GET" | "POST";
  body?: BodyInit | null;
  /** Request timeout in milliseconds. Defaults to 60s; backfill ops should pass 180_000. */
  timeoutMs?: number;
  /** Status codes that should NOT raise (e.g. 503 during degraded health). */
  acceptStatuses?: number[];
  /** Whether to set `cache: "no-store"`. Defaults to true (analytics is live data). */
  noStore?: boolean;
  /** Optional extra headers merged on top of the auth headers. */
  headers?: HeadersInit;
}

export interface VerifiedResultCorrectionRequest {
  match_id: string;
  home_score: number;
  away_score: number;
  winner?: string;
  penalty_score?: { home: number; away: number };
  source: string;
  source_url?: string;
  notes?: string;
  confirmed: boolean;
}

/**
 * Low-level fetch wrapper for analytics endpoints. Most callers should use the
 * named methods on `analyticsApi` below; this is exposed for one-off calls
 * (e.g. preview with a dynamic query string).
 */
export async function analyticsFetch<T>(
  path: string,
  options: AnalyticsFetchOptions = {},
): Promise<T> {
  const method = options.method ?? "GET";
  // Keep headers as a plain Record so they are inspectable in tests and merge
  // deterministically with extra headers (a Headers instance hides keys behind
  // .get() and would break `expect.objectContaining({ "X-API-Key": ... })`).
  const headers: Record<string, string> = {
    ...(analyticsHeaders(options.headers) as Record<string, string>),
  };
  if (options.body != null && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const noStore = options.noStore ?? true;

  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      cache: noStore ? "no-store" : undefined,
      signal: controller.signal,
      ...(options.body != null ? { body: options.body } : {}),
    });
    if (!res.ok && !options.acceptStatuses?.includes(res.status)) {
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

/**
 * Build a query string from a record of params, skipping undefined/null values.
 * Array values are appended once per element (matches the existing
 * `history_ids` repeat-pattern the backend expects).
 */
function buildQuery(params: Record<string, string | number | boolean | undefined | null | Array<string | number>>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    if (Array.isArray(value)) {
      for (const item of value) sp.append(key, String(item));
    } else {
      sp.set(key, String(value));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/**
 * Named, typed analytics API surface. Mirrors the shape of `eventsApi` so the
 * dashboard reads like a client of the API rather than a pile of `fetch()`
 * calls. Each method is a thin wrapper over `analyticsFetch`.
 */
export const analyticsApi = {
  // ---- Read-only stats (60s timeout) ----
  engineStats: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>("/analytics/engine-stats"),

  accuracyStats: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>("/analytics/accuracy-stats"),

  oddsCacheStats: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>("/analytics/odds-cache-stats"),

  systemHealth: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>("/analytics/system-health"),

  qualityLoop: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>("/analytics/quality-loop"),

  predictionCoverage: <T = unknown>(staleAfterHours = 24): Promise<T> =>
    analyticsFetch<T>(`/analytics/prediction-coverage${buildQuery({ stale_after_hours: staleAfterHours })}`),

  resultConsistency: <T = unknown>(limit = 25): Promise<T> =>
    analyticsFetch<T>(`/analytics/result-consistency${buildQuery({ limit })}`),

  consistencyRepairPlan: <T = unknown>(limit = 25): Promise<T> =>
    analyticsFetch<T>(`/analytics/consistency-repair-plan${buildQuery({ limit })}`),

  consistencyRepairPreview: <T = unknown>(historyIds: Array<number | string>): Promise<T> =>
    analyticsFetch<T>(`/analytics/consistency-repair-preview${buildQuery({ history_ids: historyIds })}`),

  postMatchBackfillRuns: <T = unknown>(limit = 5): Promise<T> =>
    analyticsFetch<T>(`/analytics/post-match-backfill/runs${buildQuery({ limit })}`),

  resultFactBackfillRuns: <T = unknown>(limit = 5): Promise<T> =>
    analyticsFetch<T>(`/analytics/result-fact-backfill/runs${buildQuery({ limit })}`),

  reconcileScoringRuns: <T = unknown>(limit = 5): Promise<T> =>
    analyticsFetch<T>(`/analytics/reconcile-scoring/runs${buildQuery({ limit })}`),

  // ---- Mutating / long operations (180s timeout) ----
  runConsistencyRepair: <T = unknown>(
    historyIds: Array<number | string>,
    dryRun: boolean,
    confirm: boolean,
  ): Promise<T> =>
    analyticsFetch<T>(
      `/analytics/consistency-repair${buildQuery({
        history_ids: historyIds,
        dry_run: dryRun ? "true" : "false",
        confirm: confirm ? "true" : "false",
      })}`,
      { method: "POST", timeoutMs: LONG_OPERATION_TIMEOUT_MS },
    ),

  runPostMatchBackfill: <T = unknown>(dryRun: boolean): Promise<T> =>
    analyticsFetch<T>(
      `/analytics/post-match-backfill${buildQuery({ dry_run: dryRun ? "true" : "false" })}`,
      { method: "POST", timeoutMs: LONG_OPERATION_TIMEOUT_MS },
    ),

  runResultFactBackfill: <T = unknown>(
    limit: number,
    dryRun: boolean,
    confirm: boolean,
  ): Promise<T> =>
    analyticsFetch<T>(
      `/analytics/result-fact-backfill${buildQuery({
        limit,
        dry_run: dryRun ? "true" : "false",
        confirm: confirm ? "true" : "false",
      })}`,
      { method: "POST", timeoutMs: LONG_OPERATION_TIMEOUT_MS },
    ),

  runReconcileScoring: <T = unknown>(): Promise<T> =>
    analyticsFetch<T>(
      "/analytics/reconcile-scoring",
      { method: "POST", timeoutMs: LONG_OPERATION_TIMEOUT_MS },
    ),

  verifiedResultCorrection: <T = unknown>(payload: VerifiedResultCorrectionRequest): Promise<T> =>
    analyticsFetch<T>(
      "/analytics/verified-result-correction",
      {
        method: "POST",
        body: JSON.stringify(payload),
        timeoutMs: LONG_OPERATION_TIMEOUT_MS,
      },
    ),

  tournamentSimulation: <T = unknown>(numSimulations = 1000, forceRefresh = false): Promise<T> =>
    analyticsFetch<T>(
      `/analytics/tournament-simulation${buildQuery({
        num_simulations: numSimulations,
        force_refresh: forceRefresh ? "true" : undefined,
      })}`,
      { timeoutMs: LONG_OPERATION_TIMEOUT_MS },
    ),
};

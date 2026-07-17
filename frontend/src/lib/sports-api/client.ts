import { getApiBase } from "@/lib/env";
import { ApiError, buildApiErrorMessage, getOperatorApiKey, getOperatorId, handleFetchError } from "@/lib/api";

const POST_TIMEOUT_MS = 60_000;

/**
 * Build a query string from a params object, omitting undefined/empty values.
 * Uses URLSearchParams for proper encoding.
 */
export function buildQuery(
  params: Record<string, string | number | undefined | boolean>,
): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== "",
  );
  if (entries.length === 0) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.set(k, String(v));
  return `?${usp.toString()}`;
}

/**
 * POST wrapper for sport mutations. Mirrors the global `swrFetcher`'s
 * auth + timeout + error-localization behavior but uses `method: "POST"`.
 * Use this for mutations that invalidate SWR cache keys via the global
 * `mutate()` from "swr".
 */
export async function sportPost<T>(
  path: string,
  body?: unknown,
  opts?: { signal?: AbortSignal },
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const operatorKey = getOperatorApiKey();
  if (operatorKey) headers["X-API-Key"] = operatorKey;
  const operatorId = getOperatorId();
  if (operatorId) headers["X-Operator"] = operatorId;

  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), POST_TIMEOUT_MS);

  try {
    const response = await fetch(`${getApiBase()}${path}`, {
      method: "POST",
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: opts?.signal ?? controller.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const bodyText = await response.text();
      throw new ApiError(response.status, buildApiErrorMessage(response.status, bodyText));
    }
    return await response.json();
  } catch (error) {
    handleFetchError(error);
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

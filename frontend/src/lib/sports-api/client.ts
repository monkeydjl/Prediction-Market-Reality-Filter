import { getApiBase } from "@/lib/env";
import { buildApiErrorMessage, getOperatorApiKey, getOperatorId } from "@/lib/api";

const POST_TIMEOUT_MS = 60_000;

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
      throw new Error(buildApiErrorMessage(response.status, bodyText));
    }
    return await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    if (error instanceof TypeError) {
      throw new Error("无法连接到服务器，请检查网络或后端服务状态");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

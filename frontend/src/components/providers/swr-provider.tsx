"use client";

import { SWRConfig } from "swr";
import {
  ApiError,
  buildApiErrorMessage,
  buildOperatorAuthHeaders,
  handleFetchError,
} from "@/lib/api";

// Default SWR fetcher. Mirrors the auth/timeout behavior of the central
// `api()` client in lib/api.ts so any `useSWR(key)` call (which falls back to
// this fetcher) automatically:
//   - injects the operator's X-API-Key / X-Operator session headers (so GETs
//     stay consistent with the rest of the authenticated surface the day a
//     key becomes required on reads);
//   - is bounded by a 60s AbortController timeout (a hung backend no longer
//     leaves the SWR cache spinning indefinitely);
//   - surfaces localized, user-readable error messages instead of
//     `Request failed: <statusText>`.
//
// The fetcher keeps `cache: "no-store"` so SWR (not the browser HTTP cache)
// owns revalidation, matching the prior behavior.
const SWR_FETCHER_TIMEOUT_MS = 60_000;

async function swrFetcher(url: string): Promise<unknown> {
  const headers: Record<string, string> = { ...buildOperatorAuthHeaders() };

  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), SWR_FETCHER_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers,
      signal: controller.signal,
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

export function SWRProvider({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher: swrFetcher,
        revalidateOnFocus: false,
        dedupingInterval: 30_000,
        errorRetryCount: 2,
      }}
    >
      {children}
    </SWRConfig>
  );
}

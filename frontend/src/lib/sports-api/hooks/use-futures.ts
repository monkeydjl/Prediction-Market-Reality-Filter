"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type {
  AvailableFuturesResponse,
  FuturesCoverageResponse,
  FuturesLinksResponse,
  FuturesSeriesRegistryResponse,
  FuturesSnapshotsResponse,
} from "../types";

export function useAvailableFutures() {
  const key = `${getApiBase()}/futures`;
  return useSWR<AvailableFuturesResponse>(key);
}

export function useFuturesCoverage() {
  const key = `${getApiBase()}/futures/meta/coverage`;
  return useSWR<FuturesCoverageResponse>(key);
}

/** Registered Kalshi series prefixes, independent of whether any pair is linked. */
export function useFuturesSeries() {
  const key = `${getApiBase()}/futures/meta/series`;
  return useSWR<FuturesSeriesRegistryResponse>(key);
}

export function useFuturesLinks(competition: string | null, season: string | null) {
  const key = competition && season
    ? `${getApiBase()}/futures/${competition}/${season}`
    : null;
  return useSWR<FuturesLinksResponse>(key);
}

export function useLatestSnapshots(competition: string | null, season: string | null) {
  const key = competition && season
    ? `${getApiBase()}/futures/${competition}/${season}/latest`
    : null;
  return useSWR<FuturesSnapshotsResponse>(key);
}

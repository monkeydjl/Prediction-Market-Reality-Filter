"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost } from "../client";
import type {
  MarketLinkList,
  LatestLink,
  SnapshotSeries,
} from "../types";

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

type LatestLinksResponse = { items: LatestLink[]; total: number };
type SnapshotsResponse = { series: SnapshotSeries[] };

export function useMarketLinks(params?: {
  match_id?: string;
  source?: string;
  verified?: boolean;
}) {
  const qs = buildQuery(params ?? {});
  const key = `${getApiBase()}/sport-markets/links${qs}`;
  return useSWR<MarketLinkList>(key);
}

export function useMarketLinksByMatch(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/links/${matchId}` : null;
  return useSWR<MarketLinkList>(key);
}

export function useLatestLinks(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/links/${matchId}/latest` : null;
  return useSWR<LatestLinksResponse>(key);
}

export function usePendingLinks() {
  const key = `${getApiBase()}/sport-markets/pending`;
  return useSWR<MarketLinkList>(key);
}

export function useMarketSnapshots(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-markets/snapshots/${matchId}` : null;
  return useSWR<SnapshotsResponse>(key);
}

export async function verifyLink(
  matchId: string,
  contractId: string,
  verified: boolean,
): Promise<void> {
  await sportPost<void>(
    `/sport-markets/links/${matchId}/${contractId}/verify`,
    { verified },
  );
  await mutate(`${getApiBase()}/sport-markets/pending`);
  await mutate(`${getApiBase()}/sport-markets/links/${matchId}`);
}

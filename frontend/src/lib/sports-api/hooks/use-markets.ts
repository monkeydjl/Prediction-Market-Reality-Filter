"use client";

import useSWR from "swr";
import { mutate } from "swr";
import { getApiBase } from "@/lib/env";
import { sportPost, buildQuery } from "../client";
import type {
  MarketLinkList,
  LatestLink,
  SnapshotSeries,
} from "../types";

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

export type AutoVerifyResult = {
  pending_total: number;
  candidates: number;
  auto_verified: number;
  would_verify?: number;
  threshold: number;
  dry_run: boolean;
  link_ids: number[];
  enabled?: boolean;
  message?: string;
};

/** Dry-run or apply high-confidence pending auto-verify (P1-V2). */
export async function autoVerifyPending(options?: {
  dry_run?: boolean;
  min_confidence?: number;
}): Promise<AutoVerifyResult> {
  const dry = options?.dry_run ?? true;
  const qs = buildQuery({
    dry_run: dry,
    min_confidence: options?.min_confidence,
  });
  const result = await sportPost<AutoVerifyResult>(
    `/sport-markets/pending/auto-verify${qs}`,
    {},
  );
  if (!dry) {
    await mutate(`${getApiBase()}/sport-markets/pending`);
  }
  return result;
}

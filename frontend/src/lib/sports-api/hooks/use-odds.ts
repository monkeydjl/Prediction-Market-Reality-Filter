"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { TraditionalOddsLatest, TraditionalOddsHistory } from "../types";

export function useTraditionalOddsLatest(matchId: string | null) {
  const key = matchId ? `${getApiBase()}/sport-odds/${matchId}/latest` : null;
  return useSWR<TraditionalOddsLatest>(key);
}

export function useTraditionalOddsHistory(matchId: string | null, mappedOutcome?: string) {
  const usp = new URLSearchParams();
  if (mappedOutcome) usp.set("mapped_outcome", mappedOutcome);
  const q = usp.toString() ? `?${usp.toString()}` : "";
  const key = matchId ? `${getApiBase()}/sport-odds/${matchId}/history${q}` : null;
  return useSWR<TraditionalOddsHistory>(key);
}

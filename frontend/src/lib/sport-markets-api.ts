import { getWorldCupApiBase } from "./env";

const API_BASE = getWorldCupApiBase();

export interface MarketLink {
  id: number;
  match_id: string;
  contract_id: string;
  source: string;
  outcome_label: string;
  mapped_outcome: string;
  link_method: string;
  link_confidence: number;
  verified: boolean;
  market_question: string | null;
  implied_prob: number;
}

export interface MarketLinkList {
  items: MarketLink[];
  total: number;
}

export interface LatestLink extends MarketLink {
  latest_snapshot: {
    id: number;
    implied_prob: number;
    price: number | null;
    captured_at: string | null;
  } | null;
}

export interface SnapshotPoint {
  id: number;
  implied_prob: number;
  price: number | null;
  captured_at: string | null;
}

export interface SnapshotSeries {
  contract_id: string;
  outcome_label: string;
  mapped_outcome: string;
  snapshots: SnapshotPoint[];
}

function buildQuery(params: Record<string, string | number | undefined | boolean>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${k}=${v}`).join("&");
}

export async function fetchMarketLinks(params?: {
  match_id?: string;
  source?: string;
  verified?: boolean;
}): Promise<MarketLinkList> {
  const qs = buildQuery(params ?? {});
  const res = await fetch(`${API_BASE}/api/sport-markets/links${qs}`);
  if (!res.ok) throw new Error("Failed to fetch market links");
  return res.json();
}

export async function fetchMarketLinksByMatch(matchId: string): Promise<MarketLinkList> {
  const res = await fetch(`${API_BASE}/api/sport-markets/links/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch links");
  return res.json();
}

export async function fetchLatestLinks(
  matchId: string,
): Promise<{ items: LatestLink[]; total: number }> {
  const res = await fetch(`${API_BASE}/api/sport-markets/links/${matchId}/latest`);
  if (!res.ok) throw new Error("Failed to fetch latest links");
  return res.json();
}

export async function fetchPendingLinks(): Promise<MarketLinkList> {
  const res = await fetch(`${API_BASE}/api/sport-markets/pending`);
  if (!res.ok) throw new Error("Failed to fetch pending links");
  return res.json();
}

export async function verifyLink(
  matchId: string,
  contractId: string,
  verified: boolean,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/sport-markets/links/${matchId}/${contractId}/verify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verified }),
    },
  );
  if (!res.ok) throw new Error("Failed to verify link");
}

export async function fetchMarketSnapshots(
  matchId: string,
): Promise<{ series: SnapshotSeries[] }> {
  const res = await fetch(`${API_BASE}/api/sport-markets/snapshots/${matchId}`);
  if (!res.ok) throw new Error("Failed to fetch snapshots");
  return res.json();
}

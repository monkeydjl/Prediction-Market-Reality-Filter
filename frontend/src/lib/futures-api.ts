"use client";

export interface FuturesPair {
  competition: string;
  season: string;
}

export interface FuturesLink {
  id: number;
  competition: string;
  season: string;
  team: string;
  contract_id: string;
  source: string;
  market_question: string | null;
  implied_prob: number | null;
  verified: boolean;
}

export interface FuturesSnapshot {
  id: number;
  link_id: number;
  team?: string;
  implied_prob: number;
  price: number | null;
  liquidity: number | null;
  volume: number | null;
  captured_at: string;
}

export interface AvailableFuturesResponse {
  pairs: FuturesPair[];
}

export interface FuturesLinksResponse {
  competition: string;
  season: string;
  links: FuturesLink[];
}

export interface FuturesSnapshotsResponse {
  competition: string;
  season: string;
  snapshots: FuturesSnapshot[];
}

function getApiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "";
  return base.replace(/\/api$/, "");
}

export async function fetchAvailableFutures(): Promise<AvailableFuturesResponse> {
  const url = `${getApiBase()}/api/futures`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function fetchFuturesLinks(
  competition: string,
  season: string
): Promise<FuturesLinksResponse> {
  const url = `${getApiBase()}/api/futures/${competition}/${season}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

export async function fetchLatestSnapshots(
  competition: string,
  season: string
): Promise<FuturesSnapshotsResponse> {
  const url = `${getApiBase()}/api/futures/${competition}/${season}/latest`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

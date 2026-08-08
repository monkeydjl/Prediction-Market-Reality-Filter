/**
 * Client for the World Cup data-ingest routes (`/api/sports/world-cup/*` in
 * `backend/app/api/routes/events.py`).
 *
 * The 40+ backend routes collapse into two regular families, so they are
 * described as data here rather than hand-written per route:
 *
 * - **Configured sources** take no body; the payload comes from server-side
 *   config (`WORLD_CUP_DATA_FILE`, feed URLs, provider API keys). Each exposes
 *   `preview` + `import`, and the two commercial providers add `test`
 *   (connectivity) and `validate` (full pipeline diagnostic).
 * - **Payload sources** take an operator-supplied JSON body and expose
 *   `preview` + `import` under a per-kind path.
 *
 * Every route except `status` and `facts` is guarded by `require_write_key`,
 * so all calls carry the shared operator auth headers.
 */

import { getApiBase } from "@/lib/env";
import {
  ApiError,
  buildApiErrorMessage,
  buildOperatorAuthHeaders,
  handleFetchError,
} from "@/lib/api";

const API_BASE = getApiBase();
const INGEST_CLIENT_SOURCE = "world-cup-ingest-console";
const PREFIX = "/sports/world-cup";
const DEFAULT_TIMEOUT_MS = 60_000;
/** Provider imports fetch remote feeds and rewrite the fact store. */
const IMPORT_TIMEOUT_MS = 300_000;

export type SourceAction = "preview" | "import" | "test" | "validate";

export interface ConfiguredSource {
  key: string;
  label: string;
  /** Path segment under `/sports/world-cup`, without the action suffix. */
  path: string;
  description: string;
  actions: SourceAction[];
  /** Operational caveat shown next to the row. */
  note?: string;
}

export interface PayloadSource {
  key: string;
  label: string;
  path: string;
  description: string;
  /** `facts` is import-only — the backend has no `facts/preview` route. */
  supportsPreview: boolean;
}

/** Sources whose payload lives in backend config, not in the request body. */
export const CONFIGURED_SOURCES: ConfiguredSource[] = [
  {
    key: "data-file",
    label: "本地数据文件",
    path: "data/source",
    description: "WORLD_CUP_DATA_FILE 指向的单个数据文件。",
    actions: ["preview", "import"],
  },
  {
    key: "bundle-file",
    label: "本地源包文件",
    path: "data/bundle/source",
    description: "WORLD_CUP_SOURCE_BUNDLE_FILE 指向的多源数据包。",
    actions: ["preview", "import"],
  },
  {
    key: "bundle-url",
    label: "远程源包",
    path: "data/bundle/url",
    description: "WORLD_CUP_SOURCE_BUNDLE_URL 指向的远程数据包。",
    actions: ["preview", "import"],
  },
  {
    key: "bundle-feeds",
    label: "原始 Feed 列表",
    path: "data/bundle/feeds",
    description: "配置的原始 World Cup feed URL 列表。",
    actions: ["preview", "import"],
  },
  {
    key: "api-football",
    label: "API-Football",
    path: "data/bundle/api-football",
    description: "API-Football 赛程、比分与阵容 feed。",
    actions: ["preview", "import", "test", "validate"],
    note: "导入前必须先跑通「流水线校验」，否则后端返回 409。",
  },
  {
    key: "football-data",
    label: "Football-Data.org",
    path: "data/bundle/football-data",
    description: "Football-Data.org 积分榜，转换为出线资格事实。",
    actions: ["preview", "import"],
  },
  {
    key: "sportmonks",
    label: "Sportmonks",
    path: "data/bundle/sportmonks",
    description: "Sportmonks 风格的 World Cup feed。",
    actions: ["preview", "import", "test", "validate"],
  },
];

/** Sources that ingest an operator-pasted JSON payload. */
export const PAYLOAD_SOURCES: PayloadSource[] = [
  {
    key: "data",
    label: "可信数据源载荷",
    path: "data",
    description: "可信 World Cup 数据源载荷，服务端转换为事实。",
    supportsPreview: true,
  },
  {
    key: "bundle",
    label: "源包载荷",
    path: "data/bundle",
    description: "一次提交多个数据源载荷的包。",
    supportsPreview: true,
  },
  {
    key: "official-csv",
    label: "官方 CSV",
    path: "official-csv",
    description: "严格官方 CSV 档案（字段校验最严格）。",
    supportsPreview: true,
  },
  {
    key: "matches",
    label: "赛程与比分",
    path: "matches",
    description: "原始赛程 / 比赛结果数据。",
    supportsPreview: true,
  },
  {
    key: "match-events",
    label: "比赛事件与红黄牌",
    path: "match-events",
    description: "比赛事件流，转换为纪律类事实。",
    supportsPreview: true,
  },
  {
    key: "lineups",
    label: "首发阵容",
    path: "lineups",
    description: "原始首发与替补阵容数据。",
    supportsPreview: true,
  },
  {
    key: "standings",
    label: "积分榜",
    path: "standings",
    description: "小组积分榜，转换为出线资格事实。",
    supportsPreview: true,
  },
  {
    key: "player-awards",
    label: "球员奖项",
    path: "player-awards",
    description: "射手榜与个人奖项数据。",
    supportsPreview: true,
  },
  {
    key: "player-status",
    label: "球员状态",
    path: "player-status",
    description: "伤病、停赛与可用性数据。",
    supportsPreview: true,
  },
  {
    key: "statistics",
    label: "技术统计",
    path: "statistics",
    description: "球队与球员技术统计数据。",
    supportsPreview: true,
  },
  {
    key: "facts",
    label: "结构化事实",
    path: "facts",
    description: '已是事实格式的数组，或 {"facts": [...]} 包装对象。',
    supportsPreview: false,
  },
];

/** Backend responses are `FlexibleResponse` dicts; these keys are the common ones. */
export interface IngestResult {
  status?: string;
  message?: string;
  ok?: boolean;
  imported?: number;
  replaced?: number;
  skipped?: number;
  error_count?: number;
  errors?: unknown;
  count?: number;
  converted_fact_count?: number;
  facts?: unknown[];
  [key: string]: unknown;
}

export interface WorldCupFact {
  kind?: string;
  team?: string;
  home_team?: string;
  away_team?: string;
  [key: string]: unknown;
}

export interface FactsResponse {
  count?: number;
  facts?: WorldCupFact[];
}

export interface ResolveResult extends IngestResult {
  dry_run?: boolean;
  resolved?: number;
  candidates?: number;
}

function ingestHeaders(withBody: boolean): Record<string, string> {
  const headers: Record<string, string> = {
    "X-Client-Source": INGEST_CLIENT_SOURCE,
    ...buildOperatorAuthHeaders(),
  };
  if (withBody) headers["Content-Type"] = "application/json";
  return headers;
}

async function ingestFetch<T>(
  path: string,
  options: { method?: "GET" | "POST"; body?: unknown; timeoutMs?: number } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? "GET",
      headers: ingestHeaders(options.body !== undefined),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) {
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
 * Parse an operator-pasted JSON payload, rejecting anything the backend's
 * `DictPayload` / `FactsPayload` models would reject anyway.
 */
export function parseIngestPayload(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) throw new Error("请先粘贴 JSON 载荷。");
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    throw new Error(`JSON 解析失败：${error instanceof Error ? error.message : "格式无效"}`);
  }
  if (parsed === null || (typeof parsed !== "object" && !Array.isArray(parsed))) {
    throw new Error("载荷必须是 JSON 对象或数组。");
  }
  return parsed;
}

export const ingestApi = {
  status: (): Promise<IngestResult> => ingestFetch<IngestResult>(`${PREFIX}/status`),

  sourceStatus: (): Promise<IngestResult> =>
    ingestFetch<IngestResult>(`${PREFIX}/data/sources/status`),

  facts: (filters: { kind?: string; team?: string } = {}): Promise<FactsResponse> => {
    const params = new URLSearchParams();
    if (filters.kind) params.set("kind", filters.kind);
    if (filters.team) params.set("team", filters.team);
    const query = params.toString();
    return ingestFetch<FactsResponse>(`${PREFIX}/facts${query ? `?${query}` : ""}`);
  },

  /** Run a no-body action against a configured source. */
  runConfigured: (
    path: string,
    action: SourceAction,
    replace = false,
  ): Promise<IngestResult> => {
    const query = action === "import" ? `?replace=${replace}` : "";
    return ingestFetch<IngestResult>(`${PREFIX}/${path}/${action}${query}`, {
      method: "POST",
      timeoutMs: action === "test" ? DEFAULT_TIMEOUT_MS : IMPORT_TIMEOUT_MS,
    });
  },

  /** Preview or import an operator-supplied payload. */
  runPayload: (
    path: string,
    action: "preview" | "import",
    payload: unknown,
    replace = false,
  ): Promise<IngestResult> => {
    const query = action === "import" ? `?replace=${replace}` : "";
    return ingestFetch<IngestResult>(`${PREFIX}/${path}/${action}${query}`, {
      method: "POST",
      body: payload,
      timeoutMs: IMPORT_TIMEOUT_MS,
    });
  },

  resolve: (dryRun: boolean, limit: number): Promise<ResolveResult> =>
    ingestFetch<ResolveResult>(
      `${PREFIX}/resolve?dry_run=${dryRun}&limit=${limit}`,
      { method: "POST", timeoutMs: IMPORT_TIMEOUT_MS },
    ),
};

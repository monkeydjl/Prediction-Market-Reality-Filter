export interface DashboardDataCacheEntry<TData> {
  data: TData;
  cachedAt: number;
}

const DASHBOARD_CACHE_TTL_MS = 60_000;
const cache = new Map<string, DashboardDataCacheEntry<unknown>>();

export function makeDashboardCacheKey({
  limit,
  offset,
  filters,
}: {
  limit: number;
  offset: number;
  filters: object;
}) {
  const stableFilters = Object.entries(filters)
    .filter(([, value]) => value != null && value !== "")
    .sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify({ limit, offset, filters: stableFilters });
}

export function getDashboardCache<TData>(
  key: string,
  now = Date.now(),
): DashboardDataCacheEntry<TData> | null {
  const entry = cache.get(key) as DashboardDataCacheEntry<TData> | undefined;
  if (!entry) return null;
  if (now - entry.cachedAt > DASHBOARD_CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return entry;
}

export function setDashboardCache<TData>(
  key: string,
  data: TData,
  now = Date.now(),
) {
  cache.set(key, { data, cachedAt: now });
}

export function clearDashboardCache() {
  cache.clear();
}

import { describe, expect, it, beforeEach } from "vitest";
import {
  clearDashboardCache,
  getDashboardCache,
  makeDashboardCacheKey,
  setDashboardCache,
} from "./dashboard-cache";

describe("dashboard cache", () => {
  beforeEach(() => {
    clearDashboardCache();
  });

  it("uses a stable key for equivalent filters", () => {
    const a = makeDashboardCacheKey({
      limit: 10,
      offset: 0,
      filters: { status: "active", category: "all" },
    });
    const b = makeDashboardCacheKey({
      limit: 10,
      offset: 0,
      filters: { category: "all", status: "active" },
    });

    expect(a).toBe(b);
  });

  it("returns cached dashboard data within the ttl", () => {
    const key = makeDashboardCacheKey({
      limit: 10,
      offset: 0,
      filters: { status: "active" },
    });
    const data = { events: [{ id: "evt-1" }], movers: [], sparklines: {}, total: 1 };

    setDashboardCache(key, data, 1_000);

    expect(getDashboardCache<typeof data>(key, 30_000)?.data).toEqual(data);
  });

  it("drops stale dashboard data after the ttl", () => {
    const key = makeDashboardCacheKey({
      limit: 10,
      offset: 0,
      filters: { status: "active" },
    });

    setDashboardCache(key, { events: [] }, 1_000);

    expect(getDashboardCache(key, 70_000)).toBeNull();
  });
});

import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import {
  useMarketLinks,
  useMarketLinksByMatch,
  useLatestLinks,
  usePendingLinks,
  useMarketSnapshots,
  verifyLink,
} from "./use-markets";
import useSWR from "swr";

describe("useMarketLinks", () => {
  it("builds key without params", () => {
    useMarketLinks();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links");
  });

  it("builds key with params", () => {
    useMarketLinks({ source: "polymarket", verified: true });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links?source=polymarket&verified=true");
  });
});

describe("useMarketLinksByMatch", () => {
  it("builds key for a matchId", () => {
    useMarketLinksByMatch("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links/m1");
  });
});

describe("useLatestLinks", () => {
  it("builds key for a matchId and does not poll by default", () => {
    useLatestLinks("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links/m1/latest", undefined);
  });

  it("passes a refresh interval when realtime is on", () => {
    useLatestLinks("m1", 15_000);
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/links/m1/latest", {
      refreshInterval: 15_000,
    });
  });

  it("holds the key null until a match is picked", () => {
    useLatestLinks(null);
    expect(useSWR).toHaveBeenCalledWith(null, undefined);
  });
});

describe("usePendingLinks", () => {
  it("builds key", () => {
    usePendingLinks();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/pending");
  });
});

describe("useMarketSnapshots", () => {
  it("builds key for a matchId", () => {
    useMarketSnapshots("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-markets/snapshots/m1");
  });
});

describe("verifyLink", () => {
  it("is a function", () => {
    expect(typeof verifyLink).toBe("function");
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePriceStream, buildWsUrl } from "./use-price-stream";

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  readyState: number = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    // Simulate async open
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.(new Event("open"));
    }, 0);
  }

  send(_data: string) {}
  close() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close"));
  }

  static reset() {
    MockWebSocket.instances = [];
  }
}

// MockWebSocket only implements the surface use-price-stream touches, so it is
// not structurally a full WebSocket ctor; cast through unknown rather than any.
global.WebSocket = MockWebSocket as unknown as typeof WebSocket;

beforeEach(() => {
  MockWebSocket.reset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("buildWsUrl", () => {
  it("targets backend :8000 when on Next dev :3000", () => {
    const original = global.window;
    // @ts-expect-error test shim
    global.window = {
      location: {
        hostname: "localhost",
        port: "3000",
        protocol: "http:",
        origin: "http://localhost:3000",
      },
    };
    try {
      expect(buildWsUrl("m1")).toBe("ws://localhost:8000/ws/matches/m1/prices");
    } finally {
      global.window = original;
    }
  });
});

describe("usePriceStream", () => {
  it("returns empty state when matchId is null", () => {
    const { result } = renderHook(() => usePriceStream(null));
    expect(result.current.updates).toEqual([]);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("connects to WebSocket and sets isConnected=true", async () => {
    const { result } = renderHook(() => usePriceStream("match-1"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(result.current.isConnected).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("match-1");
  });

  it("appends messages to updates queue", async () => {
    const { result } = renderHook(() => usePriceStream("match-1"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({
            type: "market_snapshot",
            match_id: "match-1",
            implied_prob: 0.65,
          }),
        }),
      );
    });

    expect(result.current.updates).toHaveLength(1);
    expect(result.current.updates[0].type).toBe("market_snapshot");
  });

  it("stops reconnecting when the backend says push is disabled", async () => {
    // 4503 is REALTIME_DISABLED_CLOSE_CODE. The backend used to send 503, which
    // RFC 6455 reserves, so this branch never matched: the hook left `disabled`
    // false and kept reconnecting against an endpoint that never accepts.
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => usePriceStream("match-1"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
      });

      act(() => {
        MockWebSocket.instances[0].onclose?.(
          new CloseEvent("close", { code: 4503, reason: "Realtime push disabled" }),
        );
      });

      expect(result.current.disabled).toBe(true);
      expect(result.current.error?.message).toContain("PHASE10_REALTIME_PUSH_ENABLED");

      // Past every backoff step, so a scheduled reconnect would have fired.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(MockWebSocket.instances).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sets error on WebSocket close", async () => {
    const { result } = renderHook(() => usePriceStream("match-1"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.close();
    });

    expect(result.current.isConnected).toBe(false);
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePriceStream } from "./use-price-stream";

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

global.WebSocket = MockWebSocket as any;

beforeEach(() => {
  MockWebSocket.reset();
});

afterEach(() => {
  vi.clearAllMocks();
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

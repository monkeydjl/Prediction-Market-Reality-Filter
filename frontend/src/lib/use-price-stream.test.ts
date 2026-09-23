import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, realtimeApi } from "./api";
import { OPERATOR_CREDENTIALS_EVENT } from "./operator-credentials";
import { usePriceStream } from "./use-price-stream";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    realtimeApi: { issueTicket: vi.fn() },
  };
});

type Ticket = { ticket: string; expires_in: number; subprotocol: string };
type Deferred<T> = {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(error: unknown): void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function ticket(name: string): Ticket {
  return {
    ticket: `opaque-${name}`,
    expires_in: 60,
    subprotocol: `pmrf.ticket.${name}`,
  };
}

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static constructorFailures = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readonly url: string;
  readonly protocols: string | string[] | undefined;
  readyState = 0;
  closeCalls = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string | URL, protocols?: string | string[]) {
    if (MockWebSocket.constructorFailures > 0) {
      MockWebSocket.constructorFailures -= 1;
      throw new DOMException("constructor rejected protocol", "SyntaxError");
    }
    this.url = String(url);
    this.protocols = protocols;
    MockWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  message(data: unknown): void {
    this.onmessage?.(
      new MessageEvent("message", {
        data: typeof data === "string" ? data : JSON.stringify(data),
      }),
    );
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  serverClose(code = 1006, reason = ""): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code, reason }));
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }

  send(_data: string): void {}

  static reset(): void {
    MockWebSocket.instances = [];
    MockWebSocket.constructorFailures = 0;
  }
}

const issueTicket = vi.mocked(realtimeApi.issueTicket);
const originalWebSocket = globalThis.WebSocket;

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function signalAt(index: number): AbortSignal {
  return issueTicket.mock.calls[index][0] as AbortSignal;
}

beforeEach(() => {
  MockWebSocket.reset();
  issueTicket.mockReset();
  issueTicket.mockResolvedValue(ticket("default"));
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  globalThis.WebSocket = originalWebSocket;
});

function mounted(matchId = "match-1") {
  return renderHook(({ id }) => usePriceStream(id), {
    initialProps: { id: matchId as string | null },
  });
}

async function connected(matchId = "match-1") {
  const hook = mounted(matchId);
  await flush();
  const socket = MockWebSocket.instances[0];
  act(() => socket.open());
  return { ...hook, socket };
}

function close(socket: MockWebSocket, code: number, reason = "safe fixed reason") {
  act(() => socket.serverClose(code, reason));
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("ticket handshake", () => {
  it("does not construct a socket until the ticket request succeeds", async () => {
    const pending = deferred<Ticket>();
    issueTicket.mockReturnValue(pending.promise);

    mounted();
    await flush();

    expect(issueTicket).toHaveBeenCalledTimes(1);
    expect(signalAt(0)).toBeInstanceOf(AbortSignal);
    expect(MockWebSocket.instances).toHaveLength(0);

    await act(async () => pending.resolve(ticket("from-backend")));
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("uses the complete backend subprotocol without rebuilding it or putting secrets in the URL", async () => {
    issueTicket.mockResolvedValue(ticket("opaque-123"));

    mounted("cup final/1?");
    await flush();

    const socket = MockWebSocket.instances[0];
    expect(socket.url).toContain("/api/ws/matches/cup%20final%2F1%3F/prices");
    expect(socket.url).not.toContain("opaque-123");
    expect(socket.url).not.toContain("pmrf.ticket");
    expect(socket.url).not.toContain("?");
    expect(socket.protocols).toEqual(["pmrf.ticket.opaque-123"]);
  });

  it("does not construct a socket when ticket acquisition fails", async () => {
    issueTicket.mockRejectedValue(new ApiError(401, "opaque rejection"));

    const { result } = mounted();
    await flush();

    expect(MockWebSocket.instances).toHaveLength(0);
    expect(result.current.error?.message).toContain("凭证");
    expect(result.current.isConnected).toBe(false);
  });
  it("treats 401 and 403 as terminal by status, not localized message text", async () => {
    vi.useFakeTimers();
    for (const status of [401, 403]) {
      issueTicket.mockReset();
      MockWebSocket.reset();
      issueTicket.mockRejectedValue(new ApiError(status, "opaque rejection"));
      const hook = mounted(`auth-${status}`);
      await flush();

      expect(issueTicket).toHaveBeenCalledTimes(1);
      expect(MockWebSocket.instances).toHaveLength(0);
      expect(hook.result.current.error?.message).toContain("凭证");
      await advance(300_000);
      expect(issueTicket).toHaveBeenCalledTimes(1);
      hook.unmount();
    }
  });

  it("recovers immediately from terminal auth failure when credentials change", async () => {
    vi.useFakeTimers();
    issueTicket
      .mockRejectedValueOnce(new ApiError(403, "opaque rejection"))
      .mockResolvedValueOnce(ticket("after-credential-change"));
    const hook = mounted();
    await flush();

    expect(issueTicket).toHaveBeenCalledTimes(1);
    await advance(60_000);
    expect(issueTicket).toHaveBeenCalledTimes(1);

    act(() => window.dispatchEvent(new Event(OPERATOR_CREDENTIALS_EVENT)));
    await flush();
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].protocols).toEqual([
      "pmrf.ticket.after-credential-change",
    ]);
    expect(hook.result.current.error).toBeNull();
  });

  it.each([408, 429, 500])(
    "retries transient ticket status %i with a fresh POST and then connects",
    async (status) => {
      vi.useFakeTimers();
      issueTicket
        .mockRejectedValueOnce(new ApiError(status, "opaque transient"))
        .mockResolvedValueOnce(ticket(`recovered-${status}`));
      const hook = mounted();
      await flush();

      expect(issueTicket).toHaveBeenCalledTimes(1);
      expect(MockWebSocket.instances).toHaveLength(0);
      expect(hook.result.current.error?.message).toContain("重试");
      await advance(999);
      expect(issueTicket).toHaveBeenCalledTimes(1);
      await advance(1);
      expect(issueTicket).toHaveBeenCalledTimes(2);
      expect(signalAt(0)).not.toBe(signalAt(1));
      expect(MockWebSocket.instances).toHaveLength(1);
      expect(MockWebSocket.instances[0].protocols).toEqual([
        `pmrf.ticket.recovered-${status}`,
      ]);
    },
  );

  it("retries a network ticket failure to a finite limit and can recover", async () => {
    vi.useFakeTimers();
    issueTicket
      .mockRejectedValueOnce(new TypeError("network failed"))
      .mockResolvedValueOnce(ticket("network-recovered"));
    mounted();
    await flush();

    expect(MockWebSocket.instances).toHaveLength(0);
    await advance(1_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("stops ticket retries after the shared finite failure budget is exhausted", async () => {
    vi.useFakeTimers();
    issueTicket.mockRejectedValue(new ApiError(503, "opaque transient"));
    const hook = mounted();
    await flush();

    await advance(300_000);
    const stoppedAt = issueTicket.mock.calls.length;
    expect(stoppedAt).toBe(5);
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(hook.result.current.error?.message).not.toContain("重试");
    await advance(3_600_000);
    expect(issueTicket).toHaveBeenCalledTimes(stoppedAt);
  });

  it("uses one finite budget across ticket failures and 1011 closes", async () => {
    vi.useFakeTimers();
    issueTicket
      .mockRejectedValueOnce(new ApiError(503, "first"))
      .mockResolvedValueOnce(ticket("one"))
      .mockResolvedValueOnce(ticket("two"))
      .mockRejectedValueOnce(new ApiError(503, "third"))
      .mockRejectedValue(new ApiError(503, "later"));
    const hook = mounted();
    await flush();

    await advance(1_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    close(MockWebSocket.instances[0], 1011);
    await advance(2_000);
    expect(MockWebSocket.instances).toHaveLength(2);
    close(MockWebSocket.instances[1], 1011);
    await advance(4_000);
    expect(issueTicket).toHaveBeenCalledTimes(4);
    await advance(8_000);
    expect(issueTicket).toHaveBeenCalledTimes(5);
    await advance(300_000);

    expect(issueTicket).toHaveBeenCalledTimes(5);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(hook.result.current.error?.message).not.toContain("重试");
  });

  it("does not count an aborted attempt or leave its reconnect timer behind", async () => {
    vi.useFakeTimers();
    const pending = deferred<Ticket>();
    issueTicket.mockReturnValueOnce(pending.promise);
    const hook = mounted("old");
    await flush();
    const oldSignal = signalAt(0);

    hook.rerender({ id: "new" });
    await flush();
    expect(oldSignal.aborted).toBe(true);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    await act(async () => pending.reject(new DOMException("Aborted", "AbortError")));
    await advance(300_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
  });

  it("does not create duplicate ticket reconnect timers", async () => {
    vi.useFakeTimers();
    issueTicket
      .mockRejectedValueOnce(new ApiError(503, "one failure"))
      .mockResolvedValueOnce(ticket("next"));
    mounted();
    await flush();

    await advance(1_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
    await advance(60_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
  });
});

describe("ticket response validation and socket construction", () => {
  it.each([
    ["missing ticket", { expires_in: 60, subprotocol: "valid.protocol" }],
    ["empty ticket", { ticket: "", expires_in: 60, subprotocol: "valid.protocol" }],
    ["zero ttl", { ticket: "opaque", expires_in: 0, subprotocol: "valid.protocol" }],
    ["infinite ttl", { ticket: "opaque", expires_in: Number.POSITIVE_INFINITY, subprotocol: "valid.protocol" }],
    ["missing protocol", { ticket: "opaque", expires_in: 60 }],
    ["empty protocol", { ticket: "opaque", expires_in: 60, subprotocol: "" }],
    ["invalid protocol", { ticket: "opaque", expires_in: 60, subprotocol: "invalid protocol" }],
    ["protocol separator", { ticket: "opaque", expires_in: 60, subprotocol: "invalid,protocol" }],
  ])("rejects %s without constructing a socket", async (_case, payload) => {
    vi.useFakeTimers();
    issueTicket.mockResolvedValue(payload as Ticket);
    const hook = mounted();
    await flush();

    expect(MockWebSocket.instances).toHaveLength(0);
    expect(hook.result.current.isConnected).toBe(false);
    expect(hook.result.current.error?.message).toContain("连接");
    await advance(300_000);
    expect(issueTicket).toHaveBeenCalledTimes(5);
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(hook.result.current.error?.message.includes("opaque")).toBe(false);
    expect(hook.result.current.error?.message.includes("valid.protocol")).toBe(false);
  });

  it("retries a synchronous WebSocket constructor failure and recovers", async () => {
    vi.useFakeTimers();
    MockWebSocket.constructorFailures = 1;
    issueTicket
      .mockResolvedValueOnce(ticket("spent-by-throw"))
      .mockResolvedValueOnce(ticket("constructor-recovered"));
    mounted();
    await flush();

    expect(issueTicket).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(0);
    await advance(1_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].protocols).toEqual([
      "pmrf.ticket.constructor-recovered",
    ]);
  });

  it("never exposes invalid ticket material or API keys through errors or logs", async () => {
    vi.useFakeTimers();
    const apiKey = "write-key-sensitive";
    const leakedTicket = "ticket-sensitive";
    const leakedProtocol = "protocol-sensitive with-space";
    const consoleSpies = [
      vi.spyOn(console, "log").mockImplementation(() => undefined),
      vi.spyOn(console, "info").mockImplementation(() => undefined),
      vi.spyOn(console, "warn").mockImplementation(() => undefined),
      vi.spyOn(console, "error").mockImplementation(() => undefined),
    ];
    issueTicket.mockResolvedValue({
      ticket: leakedTicket,
      expires_in: 60,
      subprotocol: leakedProtocol,
    });
    const hook = mounted();
    await flush();

    const message = hook.result.current.error?.message ?? "";
    expect(message.includes(apiKey)).toBe(false);
    expect(message.includes(leakedTicket)).toBe(false);
    expect(message.includes(leakedProtocol)).toBe(false);
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
  });
});

describe("ticket request and socket lifecycle", () => {
  it("aborts an in-flight ticket request on unmount and never builds its socket", async () => {
    const pending = deferred<Ticket>();
    issueTicket.mockReturnValue(pending.promise);
    const { unmount } = mounted();
    await flush();

    const signal = signalAt(0);
    unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => pending.resolve(ticket("too-late")));
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("aborts the old ticket and ignores its stale response when the match changes", async () => {
    const first = deferred<Ticket>();
    const second = deferred<Ticket>();
    issueTicket.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const hook = mounted("old-match");
    await flush();

    const oldSignal = signalAt(0);
    hook.rerender({ id: "new-match" });
    await flush();
    expect(oldSignal.aborted).toBe(true);

    await act(async () => first.resolve(ticket("old")));
    expect(MockWebSocket.instances).toHaveLength(0);

    await act(async () => second.resolve(ticket("new")));
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("new-match");
    expect(MockWebSocket.instances[0].protocols).toEqual(["pmrf.ticket.new"]);
  });

  it("closes the old socket without letting its close schedule a reconnect", async () => {
    vi.useFakeTimers();
    issueTicket.mockResolvedValueOnce(ticket("old")).mockResolvedValueOnce(ticket("new"));
    const hook = mounted("old-match");
    await flush();
    const oldSocket = MockWebSocket.instances[0];
    const oldClose = oldSocket.onclose;

    hook.rerender({ id: "new-match" });
    await flush();

    expect(oldSocket.closeCalls).toBe(1);
    expect(oldSocket.onclose).toBeNull();
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => oldClose?.(new CloseEvent("close", { code: 1006 })));
    await advance(60_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("does not let stale socket error or close handlers overwrite the new match state", async () => {
    vi.useFakeTimers();
    issueTicket.mockResolvedValueOnce(ticket("old")).mockResolvedValueOnce(ticket("new"));
    const hook = mounted("old-match");
    await flush();
    const oldSocket = MockWebSocket.instances[0];
    const oldError = oldSocket.onerror;
    const oldClose = oldSocket.onclose;

    hook.rerender({ id: "new-match" });
    await flush();
    const newSocket = MockWebSocket.instances[1];
    act(() => newSocket.open());
    expect(hook.result.current.isConnected).toBe(true);
    expect(hook.result.current.error).toBeNull();

    act(() => {
      oldError?.(new Event("error"));
      oldClose?.(new CloseEvent("close", { code: 1006 }));
    });
    expect(hook.result.current.isConnected).toBe(true);
    expect(hook.result.current.error).toBeNull();
    await advance(60_000);
    expect(issueTicket).toHaveBeenCalledTimes(2);
  });

  it("cancels reconnect timers and suppresses cleanup close on unmount", async () => {
    vi.useFakeTimers();
    const hook = await connected();
    close(hook.socket, 1006);
    hook.unmount();
    await advance(60_000);

    expect(issueTicket).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("retries when the operator credential changes without requiring a page refresh", async () => {
    issueTicket.mockRejectedValueOnce(new ApiError(401, "opaque rejection"));
    const { result } = mounted();
    await flush();
    expect(result.current.error).not.toBeNull();
    expect(MockWebSocket.instances).toHaveLength(0);

    issueTicket.mockResolvedValueOnce(ticket("after-key-change"));
    act(() => window.dispatchEvent(new Event(OPERATOR_CREDENTIALS_EVENT)));
    await flush();

    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(1);
  });
});

describe("close-code policy and reconnect budget", () => {
  it.each([
    [4503, "实时推送"],
    [1008, "授权"],
    [4404, "比赛"],
  ])("stops permanently for terminal close code %i", async (code, message) => {
    vi.useFakeTimers();
    const hook = await connected();

    close(hook.socket, code);
    await advance(120_000);

    expect(issueTicket).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(hook.result.current.error?.message).toContain(message);
    expect(hook.result.current.disabled).toBe(code === 4503);
  });

  it.each([1013, 1011])(
    "retries transient close code %i only to a finite limit and buys a ticket each time",
    async (code) => {
      vi.useFakeTimers();
      const hook = await connected();

      for (let attempts = 1; attempts < 10; attempts += 1) {
        const current = MockWebSocket.instances.at(-1)!;
        close(current, code);
        await advance(60_000);
        if (MockWebSocket.instances.length === attempts) break;
      }
      const stoppedAt = MockWebSocket.instances.length;
      await advance(300_000);

      expect(stoppedAt).toBeGreaterThan(1);
      expect(stoppedAt).toBeLessThanOrEqual(5);
      expect(issueTicket).toHaveBeenCalledTimes(stoppedAt);
      expect(MockWebSocket.instances).toHaveLength(stoppedAt);
      expect(hook.result.current.error).not.toBeNull();
    },
  );

  it("uses ordinary exponential backoff and never creates duplicate reconnect timers", async () => {
    vi.useFakeTimers();
    const hook = await connected();
    const first = hook.socket;

    act(() => {
      first.fail();
      first.fail();
      first.serverClose(1006);
      // Browsers fire close once; invoke it twice to prove our handler itself
      // cannot create two timers if a test double or platform does misbehave.
      first.onclose?.(new CloseEvent("close", { code: 1006 }));
    });
    expect(hook.result.current.error?.message).toContain("连接");
    expect(issueTicket).toHaveBeenCalledTimes(1);

    await advance(999);
    expect(issueTicket).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(issueTicket).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("resets transient failure count and ordinary backoff after a successful open", async () => {
    vi.useFakeTimers();
    const hook = await connected();
    close(hook.socket, 1013);
    await advance(1_000);
    const recovered = MockWebSocket.instances[1];
    act(() => recovered.open());

    close(recovered, 1013);
    await advance(999);
    expect(MockWebSocket.instances).toHaveLength(2);
    await advance(1);
    expect(MockWebSocket.instances).toHaveLength(3);
    expect(hook.result.current.disabled).toBe(false);
  });
});

describe("message validation and bounded updates", () => {
  it("ignores heartbeats, malformed JSON and unknown message types", async () => {
    const hook = await connected();

    act(() => {
      hook.socket.message({ type: "heartbeat", ts: "2026-09-10T00:00:00Z" });
      hook.socket.message("{broken-json");
      hook.socket.message({ type: "private_internal_state", match_id: "match-1" });
      hook.socket.message({ type: "market_snapshot", implied_prob: "not-a-number" });
    });

    expect(hook.result.current.updates).toEqual([]);
    expect(hook.result.current.isConnected).toBe(true);
  });

  it("rejects frames that contain only a recognized type", async () => {
    const hook = await connected();

    act(() => {
      hook.socket.message({ type: "market_snapshot" });
      hook.socket.message({ type: "odds_snapshot" });
    });

    expect(hook.result.current.updates).toEqual([]);
  });

  it.each([
    ["market_snapshot", "match_id", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market_snapshot", "link_id", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market_snapshot", "implied_prob", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market_snapshot", "price", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market_snapshot", "captured_at", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "match_id", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "outcome", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "implied_prob", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "decimal_odds", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "bookmaker", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds_snapshot", "captured_at", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: null,
      captured_at: "2026-09-10T00:00:00Z",
    }],
  ])("rejects %s when required field %s is missing or invalid", async (_type, field, valid) => {
    const hook = await connected();
    const missing = { ...valid } as Record<string, unknown>;
    delete missing[field];
    const invalid = { ...valid, [field]: field === "bookmaker" ? 123 : null };

    act(() => {
      hook.socket.message(missing);
      hook.socket.message(invalid);
    });

    expect(hook.result.current.updates).toEqual([]);
  });

  it("accepts scheduler market and odds contracts including a null bookmaker", async () => {
    const hook = await connected();

    act(() => {
      hook.socket.message({
        type: "market_snapshot",
        match_id: "match-1",
        link_id: 7,
        implied_prob: 0.61,
        price: 61,
        captured_at: "2026-09-10T00:00:00Z",
        ignored_extension: true,
      });
      hook.socket.message({
        type: "odds_snapshot",
        match_id: "match-1",
        outcome: "home_win",
        implied_prob: 0.55,
        decimal_odds: 1.82,
        bookmaker: null,
        captured_at: "2026-09-10T00:00:01Z",
      });
    });

    expect(hook.result.current.updates).toHaveLength(2);
    expect(hook.result.current.updates[0]).toMatchObject({
      type: "market_snapshot",
      link_id: 7,
      price: 61,
    });
    expect(hook.result.current.updates[1]).toMatchObject({
      type: "odds_snapshot",
      bookmaker: null,
      decimal_odds: 1.82,
    });
  });

  it.each([
    ["market match id", {
      type: "market_snapshot",
      match_id: "",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market capture time", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: 61,
      captured_at: "",
    }],
    ["odds match id", {
      type: "odds_snapshot",
      match_id: "",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: "book",
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds outcome", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: "book",
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds capture time", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: 1.82,
      bookmaker: "book",
      captured_at: "",
    }],
  ])("rejects an empty %s", async (_case, frame) => {
    const hook = await connected();

    act(() => hook.socket.message(frame));

    expect(hook.result.current.updates).toEqual([]);
  });

  it.each([
    ["market link id", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: Number.NaN,
      implied_prob: 0.61,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market implied probability", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: Number.POSITIVE_INFINITY,
      price: 61,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["market price", {
      type: "market_snapshot",
      match_id: "match-1",
      link_id: 1,
      implied_prob: 0.61,
      price: Number.NEGATIVE_INFINITY,
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["odds implied probability", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: Number.NaN,
      decimal_odds: 1.82,
      bookmaker: "book",
      captured_at: "2026-09-10T00:00:00Z",
    }],
    ["decimal odds", {
      type: "odds_snapshot",
      match_id: "match-1",
      outcome: "home_win",
      implied_prob: 0.55,
      decimal_odds: Number.POSITIVE_INFINITY,
      bookmaker: "book",
      captured_at: "2026-09-10T00:00:00Z",
    }],
  ])("rejects a non-finite %s", async (_case, frame) => {
    const hook = await connected();

    act(() => hook.socket.message(frame));

    expect(hook.result.current.updates).toEqual([]);
  });

  it("accepts an empty bookmaker because the contract permits any string or null", async () => {
    const hook = await connected();

    act(() => {
      hook.socket.message({
        type: "odds_snapshot",
        match_id: "match-1",
        outcome: "home_win",
        implied_prob: 0.55,
        decimal_odds: 1.82,
        bookmaker: "",
        captured_at: "2026-09-10T00:00:00Z",
      });
    });

    expect(hook.result.current.updates).toHaveLength(1);
    expect(hook.result.current.updates[0]?.bookmaker).toBe("");
  });

  it("accepts well-formed market and odds snapshots", async () => {
    const hook = await connected();

    act(() => {
      hook.socket.message({
        type: "market_snapshot",
        match_id: "match-1",
        link_id: 1,
        implied_prob: 0.61,
        price: 61,
        captured_at: "2026-09-10T00:00:00Z",
      });
      hook.socket.message({
        type: "odds_snapshot",
        match_id: "match-1",
        outcome: "home_win",
        implied_prob: 0.55,
        decimal_odds: 1.82,
        bookmaker: "book",
        captured_at: "2026-09-10T00:00:01Z",
      });
    });

    expect(hook.result.current.updates.map((update) => update.type)).toEqual([
      "market_snapshot",
      "odds_snapshot",
    ]);
  });

  it("keeps only the most recent 100 valid updates", async () => {
    const hook = await connected();

    act(() => {
      for (let index = 0; index < 105; index += 1) {
        hook.socket.message({
          type: "market_snapshot",
          match_id: "match-1",
          link_id: index,
          implied_prob: 0.5,
          price: 50,
          captured_at: "2026-09-10T00:00:00Z",
        });
      }
    });

    expect(hook.result.current.updates).toHaveLength(100);
    expect(hook.result.current.updates[0].link_id).toBe(5);
    expect(hook.result.current.updates[99].link_id).toBe(104);
  });
});

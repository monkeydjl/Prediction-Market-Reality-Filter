"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, realtimeApi, type WsTicketResponse } from "./api";
import { buildWsUrl } from "./env";
import { OPERATOR_CREDENTIALS_EVENT } from "./operator-credentials";

export { buildWsUrl } from "./env";

export interface MarketSnapshot {
  type: "market_snapshot";
  match_id: string;
  link_id: number;
  implied_prob: number;
  price: number;
  captured_at: string;
}

export interface OddsSnapshot {
  type: "odds_snapshot";
  match_id: string;
  outcome: string;
  implied_prob: number;
  decimal_odds: number;
  bookmaker: string | null;
  captured_at: string;
}

export type PriceUpdate =
  | (MarketSnapshot & {
      outcome?: undefined;
      decimal_odds?: undefined;
      bookmaker?: undefined;
    })
  | (OddsSnapshot & {
      link_id?: undefined;
      price?: undefined;
    });

export interface UsePriceStreamResult {
  updates: PriceUpdate[];
  isConnected: boolean;
  error: Error | null;
  disabled: boolean;
}

const MAX_UPDATES = 100;
const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;
const MAX_TRANSIENT_RETRIES = 4;

const CLOSE_PUSH_DISABLED = 4503;
const CLOSE_UNAUTHORIZED = 1008;
const CLOSE_UNKNOWN_MATCH = 4404;
const CLOSE_AT_CAPACITY = 1013;
const CLOSE_LOOKUP_UNAVAILABLE = 1011;

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

export function isPriceUpdate(value: unknown): value is PriceUpdate {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  if (record.type === "market_snapshot") {
    return (
      isNonEmptyString(record.match_id) &&
      isNumber(record.link_id) &&
      isNumber(record.implied_prob) &&
      isNumber(record.price) &&
      isNonEmptyString(record.captured_at)
    );
  }
  if (record.type === "odds_snapshot") {
    return (
      isNonEmptyString(record.match_id) &&
      isNonEmptyString(record.outcome) &&
      isNumber(record.implied_prob) &&
      isNumber(record.decimal_odds) &&
      (record.bookmaker === null || typeof record.bookmaker === "string") &&
      isNonEmptyString(record.captured_at)
    );
  }
  return false;
}

const SUBPROTOCOL_TOKEN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

function isWsTicketResponse(value: unknown): value is WsTicketResponse {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    isNonEmptyString(record.ticket) &&
    isNumber(record.expires_in) &&
    record.expires_in > 0 &&
    isNonEmptyString(record.subprotocol) &&
    SUBPROTOCOL_TOKEN.test(record.subprotocol)
  );
}

type InitializationFailure = "terminal-auth" | "terminal-client" | "transient";

function classifyInitializationFailure(error: unknown): InitializationFailure {
  if (!(error instanceof ApiError)) return "transient";
  if (error.status === 401 || error.status === 403) return "terminal-auth";
  if (error.status === 408 || error.status === 429 || error.status >= 500) {
    return "transient";
  }
  return "terminal-client";
}

function initializationError(failure: InitializationFailure): Error {
  if (failure === "terminal-auth") {
    return new Error("实时推送需要有效凭证，请在右上角更新操作员凭证。");
  }
  if (failure === "terminal-client") {
    return new Error("实时连接请求被拒绝，请检查配置后重试。");
  }
  return new Error("实时连接初始化失败，正在有限重试。");
}

function exhaustedInitializationError(): Error {
  return new Error("实时连接初始化失败，已停止自动重连。");
}

function closeError(code: number): { error: Error; terminal: boolean; disabled: boolean } {
  switch (code) {
    case CLOSE_PUSH_DISABLED:
      return { error: new Error("实时推送未启用。"), terminal: true, disabled: true };
    case CLOSE_UNAUTHORIZED:
      return { error: new Error("实时推送需要授权，当前凭证无效。"), terminal: true, disabled: false };
    case CLOSE_UNKNOWN_MATCH:
      return { error: new Error("比赛不存在或暂不可广播。"), terminal: true, disabled: false };
    case CLOSE_AT_CAPACITY:
      return { error: new Error("实时连接数已达上限，正在有限重试。"), terminal: false, disabled: false };
    case CLOSE_LOOKUP_UNAVAILABLE:
      return { error: new Error("实时服务暂时不可用，正在有限重试。"), terminal: false, disabled: false };
    default:
      return { error: new Error("实时连接已断开，正在重连。"), terminal: false, disabled: false };
  }
}

export function usePriceStream(matchId: string | null): UsePriceStreamResult {
  const [updates, setUpdates] = useState<PriceUpdate[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [disabled, setDisabled] = useState(false);
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = ++generationRef.current;
    if (!matchId) return;

    const id = matchId;
    let active = true;
    let socket: WebSocket | null = null;
    let ticketController: AbortController | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    let finiteFailures = 0;
    let reconnectScheduled = false;

    const isCurrent = () => active && generationRef.current === generation;

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      reconnectScheduled = false;
    };

    function scheduleReconnect(finite: boolean): boolean {
      if (!isCurrent() || reconnectScheduled) return false;
      if (finite) {
        finiteFailures += 1;
        if (finiteFailures > MAX_TRANSIENT_RETRIES) return false;
      }
      const delay = Math.min(reconnectDelay, MAX_RECONNECT_DELAY_MS);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
      reconnectScheduled = true;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        reconnectScheduled = false;
        void connect();
      }, delay);
      return true;
    }

    async function connect() {
      if (!isCurrent() || ticketController !== null) return;
      clearReconnectTimer();
      const controller = new AbortController();
      ticketController = controller;
      try {
        const response: unknown = await realtimeApi.issueTicket(controller.signal);
        if (!isCurrent() || controller.signal.aborted || ticketController !== controller) return;
        if (!isWsTicketResponse(response)) {
          throw new TypeError("Invalid realtime ticket response");
        }
        const nextSocket = new WebSocket(buildWsUrl(id), [response.subprotocol]);
        socket = nextSocket;

        nextSocket.onopen = () => {
          if (!isCurrent() || socket !== nextSocket) return;
          setIsConnected(true);
          setError(null);
          setDisabled(false);
          reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
          finiteFailures = 0;
        };

        nextSocket.onmessage = (event: MessageEvent) => {
          if (!isCurrent() || socket !== nextSocket) return;
          try {
            const parsed: unknown = JSON.parse(String(event.data));
            if (!isPriceUpdate(parsed)) return;
            setUpdates((previous) => {
              const next = [...previous, parsed];
              return next.length > MAX_UPDATES ? next.slice(-MAX_UPDATES) : next;
            });
          } catch {
            // Malformed server frames are ignored; the transport remains usable.
          }
        };

        nextSocket.onerror = () => {
          if (!isCurrent() || socket !== nextSocket) return;
          setError(new Error("实时连接发生错误。"));
        };

        nextSocket.onclose = (event: CloseEvent) => {
          if (!isCurrent() || socket !== nextSocket) return;
          socket = null;
          setIsConnected(false);
          const outcome = closeError(event.code);
          setError(outcome.error);
          setDisabled(outcome.disabled);
          if (outcome.terminal) return;
          const finite =
            event.code === CLOSE_AT_CAPACITY || event.code === CLOSE_LOOKUP_UNAVAILABLE;
          if (!scheduleReconnect(finite) && finite) {
            setError(exhaustedInitializationError());
          }
        };
      } catch (caught) {
        if (!isCurrent() || controller.signal.aborted || ticketController !== controller) return;
        setIsConnected(false);
        const failure = classifyInitializationFailure(caught);
        if (failure !== "transient") {
          setError(initializationError(failure));
          return;
        }
        setError(initializationError(failure));
        if (!scheduleReconnect(true)) {
          setError(exhaustedInitializationError());
        }
      } finally {
        if (ticketController === controller) ticketController = null;
      }
    }

    const reconnectForCredentialChange = () => {
      if (!isCurrent()) return;
      ticketController?.abort();
      ticketController = null;
      clearReconnectTimer();
      if (socket) {
        const oldSocket = socket;
        socket = null;
        oldSocket.onclose = null;
        oldSocket.onerror = null;
        oldSocket.onopen = null;
        oldSocket.onmessage = null;
        oldSocket.close();
      }
      setIsConnected(false);
      setError(null);
      setDisabled(false);
      reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
      finiteFailures = 0;
      void connect();
    };

    window.addEventListener(OPERATOR_CREDENTIALS_EVENT, reconnectForCredentialChange);
    void connect();

    return () => {
      active = false;
      window.removeEventListener(OPERATOR_CREDENTIALS_EVENT, reconnectForCredentialChange);
      ticketController?.abort();
      ticketController = null;
      clearReconnectTimer();
      if (socket) {
        const oldSocket = socket;
        socket = null;
        oldSocket.onclose = null;
        oldSocket.onerror = null;
        oldSocket.onopen = null;
        oldSocket.onmessage = null;
        oldSocket.close();
      }
      setUpdates([]);
      setIsConnected(false);
      setError(null);
      setDisabled(false);
    };
  }, [matchId]);

  if (!matchId) return { updates: [], isConnected: false, error: null, disabled: false };
  return { updates, isConnected, error, disabled };
}

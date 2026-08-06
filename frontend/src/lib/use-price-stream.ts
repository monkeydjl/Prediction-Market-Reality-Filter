"use client";

import { useEffect, useRef, useState } from "react";

export interface PriceUpdate {
  type: "market_snapshot" | "odds_snapshot" | "heartbeat";
  match_id?: string;
  link_id?: number;
  implied_prob?: number;
  price?: number;
  outcome?: string;
  decimal_odds?: number;
  bookmaker?: string | null;
  captured_at?: string;
  ts?: string;
}

export interface UsePriceStreamResult {
  updates: PriceUpdate[];
  isConnected: boolean;
  error: Error | null;
  disabled: boolean;
}

const MAX_UPDATES = 100;

/**
 * Resolve WebSocket origin for price push.
 *
 * - NEXT_PUBLIC_WS_ORIGIN / NEXT_PUBLIC_API_ORIGIN when set
 * - Dev (Next :3000): backend :8000 (HTTP rewrite does not cover WS)
 * - Prod static export: same origin (FastAPI hosts UI + WS)
 */
export function buildWsUrl(matchId: string): string {
  const path = `/ws/matches/${encodeURIComponent(matchId)}/prices`;

  const explicit =
    (typeof process !== "undefined" &&
      (process.env.NEXT_PUBLIC_WS_ORIGIN || process.env.NEXT_PUBLIC_API_ORIGIN)) ||
    "";
  if (explicit) {
    const base = explicit.replace(/\/$/, "").replace(/^http/, "ws");
    return `${base}${path}`;
  }

  if (typeof window === "undefined") {
    return `ws://localhost:8000${path}`;
  }

  const { hostname, port, protocol, origin } = window.location;
  const isLocalHost = ["localhost", "127.0.0.1", "::1"].includes(hostname);
  // Next dev server only rewrites HTTP /api — WebSocket must hit FastAPI.
  if (port === "3000" || (isLocalHost && port !== "8000" && port !== "")) {
    const wsProto = protocol === "https:" ? "wss" : "ws";
    return `${wsProto}://${hostname}:8000${path}`;
  }

  return `${origin.replace(/^http/, "ws")}${path}`;
}

export function usePriceStream(matchId: string | null): UsePriceStreamResult {
  const [updates, setUpdates] = useState<PriceUpdate[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [disabled, setDisabled] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(1000);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;
    if (!matchId) return;
    const id = matchId;

    // Declared as a function statement, and inside the effect, so the
    // backoff timer below can call it recursively. As a `useCallback` const
    // it referenced its own binding before initialization.
    function connect() {
      if (unmountedRef.current) return;

      const ws = new WebSocket(buildWsUrl(id));
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmountedRef.current) return;
        setIsConnected(true);
        setError(null);
        setDisabled(false);
        reconnectDelayRef.current = 1000;
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data) as PriceUpdate;
          if (data.type === "heartbeat") return;
          setUpdates((prev) => {
            const next = [...prev, data];
            return next.length > MAX_UPDATES ? next.slice(-MAX_UPDATES) : next;
          });
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onerror = () => {
        if (unmountedRef.current) return;
        setError(new Error("WebSocket connection error"));
      };

      ws.onclose = (ev: CloseEvent) => {
        if (unmountedRef.current) return;
        setIsConnected(false);
        wsRef.current = null;

        // Backend closes with 503 when PHASE10_REALTIME_PUSH_ENABLED is false
        if (ev.code === 503 || (ev.code === 1000 && /disabled|disabled/i.test(ev.reason || ""))) {
          setDisabled(true);
          setError(
            new Error(
              "实时推送未启用。请设置 PHASE10_REALTIME_PUSH_ENABLED=true 并重启后端。",
            ),
          );
          return;
        }

        // Exponential backoff reconnect
        const delay = Math.min(reconnectDelayRef.current, 30000);
        reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 30000);
        reconnectTimerRef.current = setTimeout(() => {
          connect();
        }, delay);
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect on unmount
        wsRef.current.close();
        wsRef.current = null;
      }
      // Drop this match's buffer so a later reconnect does not replay it.
      // Previously done in the effect body under `if (!matchId)`, which is a
      // synchronous setState during the effect.
      setUpdates([]);
      setIsConnected(false);
      setError(null);
      setDisabled(false);
    };
  }, [matchId]);

  // With no match selected there is no socket, so report the idle state
  // directly rather than resetting the four state slots from the effect.
  if (!matchId) {
    return { updates: [], isConnected: false, error: null, disabled: false };
  }

  return { updates, isConnected, error, disabled };
}

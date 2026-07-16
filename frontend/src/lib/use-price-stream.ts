"use client";

import { useEffect, useRef, useState, useCallback } from "react";

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
}

const MAX_UPDATES = 100;

function buildWsUrl(matchId: string): string {
  const base = (
    typeof window !== "undefined" ? window.location.origin : "http://localhost:3000"
  ).replace(/^http/, "ws");
  return `${base}/ws/matches/${matchId}/prices`;
}

export function usePriceStream(matchId: string | null): UsePriceStreamResult {
  const [updates, setUpdates] = useState<PriceUpdate[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(1000);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!matchId) return;

    const ws = new WebSocket(buildWsUrl(matchId));
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setError(null);
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
      setError(new Error("WebSocket connection error"));
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;
      // Exponential backoff reconnect
      const delay = Math.min(reconnectDelayRef.current, 30000);
      reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 30000);
      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, delay);
    };
  }, [matchId]);

  useEffect(() => {
    if (!matchId) {
      setUpdates([]);
      setIsConnected(false);
      setError(null);
      return;
    }

    connect();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect on unmount
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [matchId, connect]);

  return { updates, isConnected, error };
}

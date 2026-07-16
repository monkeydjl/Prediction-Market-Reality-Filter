"use client";

import useSWR from "swr";
import { eventsApi, type ApiHealth } from "@/lib/api";

/**
 * Real-time backend health indicator.
 *
 * Polls the /api/health endpoint every 30s via SWR and reflects the actual
 * backend connection state:
 *  - green + pulse: online (status="ok")
 *  - amber:         degraded (status="degraded", e.g. scheduler down)
 *  - red:           offline (fetch failed / network error)
 *  - muted:         connecting (initial load, no data yet)
 *
 * SSR-safe: initial render always shows "connecting" so there is no
 * hydration mismatch — the real state is set after mount via SWR.
 */

type ConnectionState = "connecting" | "online" | "degraded" | "offline";

const STATE_META: Record<ConnectionState, { color: string; label: string; pulse: boolean }> = {
  connecting: { color: "bg-muted-foreground", label: "连接中", pulse: false },
  online: { color: "bg-pos", label: "实时情报通道", pulse: true },
  degraded: { color: "bg-warn", label: "服务降级", pulse: true },
  offline: { color: "bg-neg", label: "离线", pulse: false },
};

function deriveState(data: ApiHealth | undefined, error: unknown): ConnectionState {
  if (error) return "offline";
  if (!data) return "connecting";
  return data.status === "ok" ? "online" : "degraded";
}

export function LiveStatusIndicator() {
  const { data, error } = useSWR<ApiHealth>(
    "live-status-health",
    () => eventsApi.health(),
    {
      revalidateOnFocus: false,
      dedupingInterval: 20_000,
      refreshInterval: 30_000,
      errorRetryCount: 2,
      errorRetryInterval: 10_000,
    },
  );

  const state = deriveState(data, error);
  const meta = STATE_META[state];

  return (
    <span
      className="hidden items-center gap-1.5 font-mono sm:flex"
      title={meta.label}
      aria-label={meta.label}
    >
      <span
        className={`size-1.5 rounded-full ${meta.color} ${meta.pulse ? "animate-pulse" : ""}`}
        aria-hidden="true"
      />
      {meta.label}
    </span>
  );
}

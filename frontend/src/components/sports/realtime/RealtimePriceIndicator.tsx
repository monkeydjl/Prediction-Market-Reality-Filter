"use client";

interface RealtimePriceIndicatorProps {
  isConnected: boolean;
  matchId?: string | null;
}

export function RealtimePriceIndicator({
  isConnected,
  matchId,
}: RealtimePriceIndicatorProps) {
  if (matchId === null) {
    return null;
  }

  const label = isConnected ? "LIVE" : "OFFLINE";
  const colorClass = isConnected
    ? "border-pos text-pos"
    : "border-border text-muted-foreground";

  return (
    <span
      data-testid="realtime-indicator"
      className={`ml-2 rounded border px-1.5 py-0.5 text-xs font-semibold ${colorClass}`}
    >
      {label}
    </span>
  );
}

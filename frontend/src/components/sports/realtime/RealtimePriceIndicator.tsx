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
    ? "border-green-500 text-green-600"
    : "border-gray-400 text-gray-500";

  return (
    <span
      data-testid="realtime-indicator"
      className={`ml-2 rounded border px-1.5 py-0.5 text-xs font-semibold ${colorClass}`}
    >
      {label}
    </span>
  );
}

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

  const color = isConnected ? "green" : "gray";
  const label = isConnected ? "LIVE" : "OFFLINE";

  return (
    <span
      style={{
        color,
        fontSize: "0.75rem",
        fontWeight: 600,
        padding: "2px 6px",
        border: `1px solid ${color}`,
        borderRadius: "3px",
        marginLeft: "8px",
      }}
      data-testid="realtime-indicator"
    >
      {label}
    </span>
  );
}

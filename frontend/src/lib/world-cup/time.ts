const HAS_TIME_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/;

const BEIJING_TIME_ZONE = "Asia/Shanghai";

export function parseWorldCupUtcDate(isoString: string): Date {
  return new Date(HAS_TIME_ZONE.test(isoString) ? isoString : `${isoString}Z`);
}

export function getWorldCupKickoffTime(isoString: string | null): number {
  // `MatchFixtureRecord.kickoff_utc` is `string | null`: the route writes it as
  // `m.kickoff_utc.isoformat() if m.kickoff_utc else None`, so the producer can
  // emit a null even though the column is NOT NULL. An unknown kickoff sorts
  // last rather than to the epoch -- a missing date is not 1970.
  if (!isoString) return Number.POSITIVE_INFINITY;
  return parseWorldCupUtcDate(isoString).getTime();
}

export function formatBeijingMatchDateTime(isoString: string | null): string {
  // `MatchFixtureRecord.kickoff_utc` is nullable on the wire (same producer as
  // above), and the dash is the fallback the date-only formatter already uses.
  if (!isoString) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parseWorldCupUtcDate(isoString));
}

export function formatBeijingMatchDate(isoString: string | null): string {
  // Same nullable source as `getWorldCupKickoffTime`; the dash matches the
  // fallback the prediction card already uses for a null timestamp.
  if (!isoString) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(parseWorldCupUtcDate(isoString));
}

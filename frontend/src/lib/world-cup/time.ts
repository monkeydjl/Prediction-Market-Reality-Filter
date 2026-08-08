const HAS_TIME_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/;

const BEIJING_TIME_ZONE = "Asia/Shanghai";

export function parseWorldCupUtcDate(isoString: string): Date {
  return new Date(HAS_TIME_ZONE.test(isoString) ? isoString : `${isoString}Z`);
}

export function getWorldCupKickoffTime(isoString: string): number {
  return parseWorldCupUtcDate(isoString).getTime();
}

export function formatBeijingMatchDateTime(isoString: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parseWorldCupUtcDate(isoString));
}

export function formatBeijingMatchDate(isoString: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: BEIJING_TIME_ZONE,
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(parseWorldCupUtcDate(isoString));
}

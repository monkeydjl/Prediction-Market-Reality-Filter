/**
 * Canonical hrefs for the sports pages whose id is only known at runtime.
 *
 * The frontend ships as a Next.js static export (`output: "export"`) and is
 * served by FastAPI through `StaticFiles(html=True)` with no SPA catch-all, so
 * every URL must exist as a real file in `frontend/out`. Match ids come from
 * live fixtures and cannot be enumerated by `generateStaticParams()` at build
 * time, so they travel as query params against a single prerendered page.
 *
 * Import these helpers instead of hand-writing the URLs — the query-param shape
 * is a build constraint, not a cosmetic choice.
 */

export type MatchDetailTab = "details" | "edge" | "odds" | "realtime";

/** `/sports/match/?id=<matchId>` (+ optional `&tab=`). */
export function matchDetailHref(matchId: string, tab?: MatchDetailTab): string {
  const base = `/sports/match/?id=${encodeURIComponent(matchId)}`;
  return tab ? `${base}&tab=${tab}` : base;
}

/** `/sports/learning/history/?matchId=<matchId>` */
export function learningHistoryHref(matchId: string): string {
  return `/sports/learning/history/?matchId=${encodeURIComponent(matchId)}`;
}

/**
 * `/sports/futures/?competition=<comp>&season=<season>`
 *
 * Futures pairs are registry- and link-driven, so the (competition, season)
 * key is a runtime value and takes the same query-param shape as match ids.
 */
export function futuresPairHref(competition: string, season: string): string {
  return `/sports/futures/?competition=${encodeURIComponent(competition)}&season=${encodeURIComponent(season)}`;
}

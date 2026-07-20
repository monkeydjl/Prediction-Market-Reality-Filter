export * from "./types";
export { sportPost, buildQuery } from "./client";
export { ApiError } from "@/lib/api";
export {
  useMatches,
  useMatchDetail,
  triggerPrediction,
  useEngines,
  useEnginesMeta,
} from "./hooks/use-matches";
export type { EnginesMeta, MatchListFilters } from "./hooks/use-matches";
export {
  useBettingCatalog,
} from "./hooks/use-betting-catalog";
export type {
  BettingCatalog,
  BettingCatalogCompetition,
  BettingCatalogTool,
} from "./hooks/use-betting-catalog";
export {
  useEngineScores,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
} from "./hooks/use-learning";
export {
  useMarketLinks,
  useMarketLinksByMatch,
  useLatestLinks,
  usePendingLinks,
  useMarketSnapshots,
  verifyLink,
  autoVerifyPending,
} from "./hooks/use-markets";
export {
  useTraditionalOddsLatest,
  useTraditionalOddsHistory,
} from "./hooks/use-odds";
export {
  useRecommendation,
  useOpenDecisions,
  useTopPicks,
} from "./hooks/use-recommendations";
export {
  useSettlement,
  useSettlementHistory,
  useCalibrations,
  processSettlement,
} from "./hooks/use-settlements";
export {
  useAvailableFutures,
  useFuturesCoverage,
  useFuturesLinks,
  useLatestSnapshots,
} from "./hooks/use-futures";
export {
  useOptimizationParams,
  triggerOptimization,
  triggerIngest,
  useTaskStatus,
  applyParams,
} from "./hooks/use-optimization";
export type { TaskStatus } from "./hooks/use-optimization";
export {
  parseOptimizationTaskResult,
  toMetricBarPoints,
  toCandidateBarPoints,
} from "./backtest-results";
export type {
  SportBacktestMetrics,
  ParsedOptimizationTaskResult,
  MetricBarPoint,
} from "./backtest-results";
export {
  useEdgeLatest,
  useEdgeHistory,
  useEdgeDiscrepancies,
  type FactorDriver,
  detectEdges,
} from "./hooks/use-edges";
export type {
  EdgeSource,
  EdgeResult,
  EdgeLatestResponse,
  EdgeHistoryPoint,
  EdgeHistoryResponse,
  EdgeDiscrepancyItem,
  EdgeDiscrepanciesResponse,
} from "./hooks/use-edges";
export type { AutoVerifyResult } from "./hooks/use-markets";

export * from "./types";
export { sportPost, buildQuery } from "./client";
export { ApiError } from "@/lib/api";
export {
  useMatches,
  useMatchDetail,
  triggerPrediction,
  useEngines,
  useEnginesMeta,
  syncSchedule,
} from "./hooks/use-matches";
export type {
  EnginesMeta,
  MatchListFilters,
  ScheduleSyncResult,
} from "./hooks/use-matches";
export {
  useBettingCatalog,
  useBettingStatus,
} from "./hooks/use-betting-catalog";
export type {
  BettingCatalog,
  BettingCatalogCompetition,
  BettingCatalogTool,
  BettingCatalogFlags,
  BettingStatus,
} from "./hooks/use-betting-catalog";
export {
  useEngineScores,
  useEngineScore,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
  useConfidenceReliability,
  processOutcome,
  refreshConditionalCalibration,
} from "./hooks/use-learning";
export type {
  SingleEngineScore,
  ConditionalCalibrationResult,
} from "./hooks/use-learning";
export {
  parseCalibrationKey,
  matchesCompetition,
  CONFIDENCE_BUCKET_PREFIX,
  STAGE_BUCKET_PREFIX,
} from "./calibration-buckets";
export type {
  ParsedCalibrationKey,
  CalibrationBucketKind,
} from "./calibration-buckets";
export {
  useMarketLinks,
  useMarketLinksByMatch,
  useLatestLinks,
  useLinkAudit,
  useMatchAudit,
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
  useFuturesSeries,
  useLatestSnapshots,
} from "./hooks/use-futures";
export {
  useOptimizationParams,
  useAppliedParams,
  useLiveEvidence,
  triggerOptimization,
  triggerIngest,
  useTaskStatus,
  applyParams,
  backfillAndSeed,
} from "./hooks/use-optimization";
export type {
  TaskStatus,
  BackfillSeedResult,
  LiveEvidenceGroup,
  LiveEvidenceReport,
} from "./hooks/use-optimization";
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

// Consolidated type definitions for the unified sports-api module.
// Each interface is copied verbatim from its original client file.

// From lib/sports-api.ts
export interface MatchSummary {
  match_id: string;
  sport: string;
  competition: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  has_prediction: boolean;
}

export interface MatchDetail {
  match_id: string;
  sport: string;
  competition: string;
  season_key: string;
  home_team: string;
  away_team: string;
  home_code: string;
  away_code: string;
  kickoff_utc: string | null;
  stage: string;
  round: string | null;
}

export interface ContributionItem {
  factor: string;
  direction: string;
  weight: number;
  available: boolean;
  detail: string | null;
  predicted_outcome: string | null;
}

export interface PredictionResult {
  // Optional: present in POST /predict response, absent in GET /matches/{id} prediction
  match_id?: string;
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  explanation: ContributionItem[];
  feature_version: string;
  prediction_timestamp: string | null;
  betting_analysis?: Record<string, unknown> | null;
}

// From lib/learning-api.ts
export interface EngineScoreItem {
  engine: string;
  competition: string | null;
  accuracy: number;
  avg_mae: number;
  brier_score: number;
  sample_count: number;
  confidence_calibration: number;
  last_updated: string | null;
}

export interface PredictionHistoryItem {
  id: number;
  match_id: string;
  sport: string | null;
  competition: string | null;
  engine: string;
  predicted_scores: Record<string, number>;
  outcome_probabilities: Record<string, number>;
  confidence: number;
  feature_version: string;
  trigger: string;
  created_at: string;
  outcome: {
    home_score: number;
    away_score: number;
    outcome: string;
    outcome_correct: number | null;
    score_mae: number | null;
    brier_score: number | null;
    finished_at: string | null;
  } | null;
}

export interface PredictionHistoryList {
  items: PredictionHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PredictionTrajectory {
  match_id: string;
  sport: string | null;
  competition: string | null;
  items: PredictionHistoryItem[];
  count: number;
}

export interface CalibrationItem {
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_confidence: number;
  avg_accuracy: number;
  last_updated: string | null;
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  center: number;
  avg_predicted: number | null;
  actual_frequency: number | null;
  count: number;
}

export interface ReliabilityData {
  ece?: number | null;
  max_calibration_error?: number | null;
  sample_count?: number;
  engine: string | null;
  competition: string | null;
  bins: ReliabilityBin[];
  total_samples: number;
  /**
   * Set to a stable category (currently only `"query_failed"`) when the store
   * could not be read. Absent on success, including the genuinely-empty case.
   *
   * Without it, `total_samples: 0` meant both "nothing has settled yet" and
   * "the query blew up", so a broken calibration panel rendered as an idle one.
   * The optional markers on the six fields above are what that mismatch used to
   * force; the backend now returns the same keys on both paths.
   */
  error?: string;
}

/**
 * Same bin shape as ReliabilityData, but the x axis is the engine's *stated*
 * confidence rather than max(outcome_probabilities) — a different quantity.
 * `signed_gap` is mean_confidence - mean_accuracy: positive = overconfident.
 * ECE alone is unsigned and cannot say which way to move the formula.
 */
export interface ConfidenceReliabilityData extends ReliabilityData {
  mean_confidence?: number | null;
  mean_accuracy?: number | null;
  signed_gap?: number | null;
}

// From lib/sport-markets-api.ts
export interface MarketLink {
  id: number;
  match_id: string;
  contract_id: string;
  source: string;
  outcome_label: string;
  mapped_outcome: string;
  link_method: string;
  link_confidence: number;
  verified: boolean;
  market_question: string | null;
  implied_prob: number;
}

export interface MarketLinkList {
  items: MarketLink[];
  total: number;
}

export interface LatestLink extends MarketLink {
  latest_snapshot: {
    id: number;
    implied_prob: number;
    price: number | null;
    captured_at: string | null;
  } | null;
}

export interface SnapshotPoint {
  id: number;
  implied_prob: number;
  price: number | null;
  captured_at: string | null;
}

export interface SnapshotSeries {
  contract_id: string;
  outcome_label: string;
  mapped_outcome: string;
  snapshots: SnapshotPoint[];
}

// From lib/sport-odds-api.ts
export interface TraditionalOddsSnapshot {
  implied_prob: number;
  decimal_odds: number;
  bookmaker: string | null;
  bookmakers_count: number;
  captured_at: string | null;
}

export interface TraditionalOddsSeries {
  mapped_outcome: string;
  snapshots: TraditionalOddsSnapshot[];
}

export interface TraditionalOddsHistory {
  match_id: string;
  series: TraditionalOddsSeries[];
  skipped: boolean;
  skip_reason: string | null;
}

/** `sport-odds/{id}/latest` folds the outcome key into each row. */
export interface TraditionalOddsLatestOutcome extends TraditionalOddsSnapshot {
  mapped_outcome: string;
}

export interface TraditionalOddsLatest {
  match_id: string;
  outcomes: TraditionalOddsLatestOutcome[];
  skipped: boolean;
  skip_reason: string | null;
}

// From lib/sport-recommendations-api.ts
export interface SportRecommendation {
  match_id: string;
  mapped_outcome: string;
  direction: string;
  decision: string;
  confidence: string;
  risk_level: string;
  edge_pct: number;
  raw_edge_pct: number;
  trust: number;
  liquidity_factor: number;
  stale: boolean;
  suggested_allocation_pct: number;
  calibration_status: string;
  rationale: string;
  engine_name: string | null;
  competition: string | null;
  prediction_timestamp: string | null;
  model_prob: number;
  market_prob: number;
  sources_count: number;
  captured_at: string | null;
  review_priority?: string;
  guardrail_flags?: string[] | null;
  policy_notes?: string | null;
}

export interface RecommendationList {
  items: SportRecommendation[];
  total: number;
}

// From lib/sport-settlements-api.ts
export interface MarketSettlement {
  id: number;
  match_id: string;
  mapped_outcome: string;
  engine: string;
  competition: string;
  settlement_implied_prob: number | null;
  settlement_captured_at: string | null;
  link_id: number | null;
  model_prob: number | null;
  market_prob_at_detection: number | null;
  raw_edge: number | null;
  adjusted_edge: number | null;
  brier_score: number | null;
  signed_error: number | null;
  direction_correct: number | null;
  status: string;
  skip_reason: string | null;
  match_finished_at: string;
  processed_at: string;
}

export interface MarketCalibration {
  id: number;
  engine: string;
  competition: string;
  slope: number;
  intercept: number;
  sample_count: number;
  avg_brier: number;
  avg_signed_error: number;
  direction_accuracy: number;
  last_updated: string;
}

export interface SettlementList {
  items: MarketSettlement[];
  total: number;
}

export interface CalibrationList {
  items: MarketCalibration[];
  total: number;
}

// From lib/futures-api.ts
export interface FuturesMultiLegIntegrity {
  status: "ok" | "thin" | "warn" | "incomplete" | string;
  leg_count: number;
  unique_team_count: number;
  teams: string[];
  duplicate_teams: string[];
  missing_price_count: number;
  sum_implied_prob: number | null;
  issues: string[];
}

export interface FuturesPair {
  competition: string;
  season: string;
  verified_count?: number;
  integrity?: FuturesMultiLegIntegrity;
}

export interface FuturesLink {
  id: number;
  competition: string;
  season: string;
  team: string;
  contract_id: string;
  source: string;
  market_question: string | null;
  implied_prob: number | null;
  verified: boolean;
}

export interface FuturesSnapshot {
  id: number;
  link_id: number;
  team?: string;
  implied_prob: number;
  price: number | null;
  liquidity: number | null;
  volume: number | null;
  captured_at: string;
}

export interface AvailableFuturesResponse {
  pairs: FuturesPair[];
}

export interface FuturesLinksResponse {
  competition: string;
  season: string;
  links: FuturesLink[];
  integrity?: FuturesMultiLegIntegrity;
}

export interface FuturesSnapshotsResponse {
  competition: string;
  season: string;
  snapshots: FuturesSnapshot[];
  integrity?: FuturesMultiLegIntegrity;
}

export interface FuturesSeriesRegistryEntry {
  series_prefix: string;
  competition: string;
  championship_type: string;
}

/** `GET /futures/meta/series` — the registered Kalshi series prefixes. */
export interface FuturesSeriesRegistryResponse {
  series: FuturesSeriesRegistryEntry[];
  competition_count: number;
  series_count: number;
  competitions: string[];
}

export interface FuturesCoverageResponse {
  series_registry: FuturesSeriesRegistryEntry[];
  pairs: Array<{
    competition: string;
    season: string;
    link_count: number;
    verified_count: number;
    integrity: FuturesMultiLegIntegrity;
  }>;
  pair_count: number;
  status_counts: Record<string, number>;
  registered_competitions: string[];
  linked_competitions: string[];
  missing_linked_competitions: string[];
}

// From lib/optimization-api.ts
export interface OptimizedParams {
  id: number;
  sport: string;
  competition: string;
  factor_weights: string;
  elo_params: string;
  score: number;
  accuracy: number;
  brier_score: number;
  mae: number;
  sample_count: number;
  trial_number: number | null;
  status: string;
  created_at: string | null;
  applied_at: string | null;
}

/** Response of POST /sport-optimization/apply/{id} */
export interface ApplyParamsResult {
  applied: OptimizedParams;
  previous_applied: OptimizedParams | null;
  weight_diff: Array<{
    factor: string;
    before: number | null;
    after: number | null;
  }>;
}

export interface MarketPriceAudit {
  link_id: number;
  available: boolean;
  snapshot_count: number;
  first_price?: number;
  last_price?: number;
  delta_pp?: number;
  max_drawdown_pp?: number;
  min_price?: number;
  max_price?: number;
  first_captured_at?: string | null;
  last_captured_at?: string | null;
  flags?: string[];
  source?: string;
  market_id?: string;
  verified?: boolean;
  mapped_outcome?: string;
  match_id?: string;
}

export interface MatchMarketAudit {
  match_id: string;
  link_count: number;
  audits: MarketPriceAudit[];
}

// Shapes mirror the FastAPI event-record contract (see app/models/event.py
// and event_intelligence_service.build_event_record). Permissive on purpose:
// the backend allows extra keys and many fields are best-effort.

export interface Probability {
  baseline?: number;
  estimated?: number;
  change?: number;
  direction?: string;
}

export interface Credibility {
  score?: number;
  level?: string;
  confidence?: number;
  news_quality?: number;
  evidence_strength?: number;
  source_count?: number;
}

export interface Impact {
  score?: number;
  level?: string;
  drivers?: string[];
}

export interface IntelligenceReport {
  headline?: string;
  why_it_matters?: string;
  probability_assessment?: string;
  recommended_action?: string;
}

export interface EventSource {
  platform?: string;
  type?: string;
  event_type?: string;
  source_id?: string;
  baseline_probability?: number;
  volume?: number;
  liquidity?: number;
  url?: string;
}

export interface Semantics {
  resolution_criteria?: string;
  time_horizon?: string;
  entities?: string[];
}

export interface Outcome {
  status?: string;
  actual_outcome?: number;
  confidence?: number;
  resolved_at?: string;
  source?: string;
  notes?: string;
}

export interface Calibration {
  brier_score?: number;
  skill_score?: number;
  grade?: string;
  estimated_probability?: number;
  actual_outcome?: number;
  trajectory_observations?: number;
  trajectory_span_hours?: number | null;
}

export interface CrossValidation {
  model?: string;
  probability?: number;
  agreement?: string;
  divergence?: number;
}

export interface EvidenceAggregate {
  direction?: string;
  strength?: number;
  conflict?: number;
  freshness?: number;
  resolution_relevance?: number;
  source_count?: number;
}

// One collected piece of source evidence (news article / official release).
// kind groups it in the detail UI: "official" vs "news".
export interface EvidenceItem {
  kind?: string;
  source?: string;
  title?: string;
  title_zh?: string;
  summary?: string;
  summary_zh?: string;
  url?: string;
  published?: string;
  quality?: number;
  relevance?: number;
}

// Human tracking decision for an event.
export interface Tracking {
  status?: string; // tracking | watching | archived
  priority?: string; // high | medium | low
}

export interface EventRecord {
  event_id: string;
  event_title: string;
  event_title_zh?: string;
  event_summary?: string;
  probability?: Probability;
  credibility?: Credibility;
  impact?: Impact;
  evidence?: EvidenceAggregate;
  evidence_items?: EvidenceItem[];
  tracking?: Tracking | null;
  source?: EventSource;
  value_score?: number;
  intelligence_report?: IntelligenceReport;
  semantics?: Semantics | null;
  outcome?: Outcome | null;
  calibration?: Calibration | null;
  cross_validation?: CrossValidation | null;
}

export interface TrackedEntry {
  event_id: string;
  first_seen?: string;
  last_updated?: string;
  record: EventRecord;
}

export interface Trend {
  observations?: number;
  direction?: string;
  pattern?: string;
  net_change?: number;
  recent_change?: number;
  min_probability?: number;
  max_probability?: number;
  volatility?: number;
  latest_probability?: number;
  span_hours?: number | null;
}

export interface Mover {
  event_id: string;
  event_title?: string;
  event_title_zh?: string;
  trend?: Trend;
}

export interface HistorySnapshot {
  timestamp?: string;
  estimated?: number;
  change?: number;
  direction?: string;
}

export interface SimilarEvent {
  event_id: string;
  event_title: string;
  event_title_zh?: string;
  similarity?: number;
  estimated_probability?: number;
  change?: number;
}

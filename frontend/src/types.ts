/** Shapes returned by the backend. Mirrors the Pydantic models in agents/schemas.py. */

export type Confidence = 'high' | 'moderate' | 'low'
export type Severity = 'high' | 'medium' | 'low'
export type Verdict = 'supported' | 'unsupported' | 'misrepresented'

export interface SourceRef {
  ref_id: string
  kind: string
  label: string
  url: string | null
  fetched_at: string
  from_cache: boolean
  detail: Record<string, unknown>
}

export interface Claim {
  claim: string
  evidence_ref: string
  reasoning: string
  dimension: string
}

export interface DirectionalThesis {
  thesis: string
  supporting_points: Claim[]
  key_assumption: string
  biggest_risk_to_thesis: string
  what_would_change_my_mind: string
  confidence: Confidence
  confidence_reasoning: string
  evidence_gaps: string[]
}

export interface RiskItem {
  risk: string
  severity: Severity
  evidence_ref: string
  why_it_matters: string
}

export interface RiskAssessment {
  risks: RiskItem[]
  overall_risk_rating: Severity
  rating_reasoning: string
  volatility_note: string
}

export interface Rebuttal {
  targets_claim: string
  critique_type: string
  critique: string
  evidence_ref: string | null
  concession: boolean
}

export interface RebuttalSet {
  rebuttals: Rebuttal[]
  strongest_opposing_point: string
  position_after_debate: string
}

export interface MemoPoint {
  point: string
  evidence_ref: string
  survived_debate: boolean
}

export interface ResearchMemo {
  plain_summary: string
  confidence: Confidence
  confidence_reasoning: string
  key_risks: string[]
  bull_case: MemoPoint[]
  bear_case: MemoPoint[]
  bull_needs_to_be_true: string[]
  bear_needs_to_be_true: string[]
  how_the_debate_went: string
  user_view_assessment: string | null
  what_this_means_for_you: string
}

export interface VerificationFinding {
  verdict: Verdict
  claim: string
  evidence_ref: string
  explanation: string
}

export interface VerificationReport {
  findings: VerificationFinding[]
  summary: string
  overall_verdict: 'clean' | 'minor_issues' | 'significant_issues'
}

export interface ChartPoint {
  date: string
  close: number
}

export interface RunPayload {
  run_id: string
  status: string
  stage: string
  ok?: boolean
  error?: string | null
  degraded?: string[]
  elapsed_s?: number
  ticker: string | null
  company_name: string | null
  horizon: string
  user_view: string | null
  profile?: { sector?: string; industry?: string; summary?: string; website?: string }
  chart?: {
    series: ChartPoint[]
    latest_close: number | null
    as_of: string | null
    sma_50: number | null
    sma_200: number | null
    range_52w: Record<string, number | null>
    returns_pct: Record<string, number | null>
    volatility_pct: number | null
  }
  bull: DirectionalThesis | null
  bear: DirectionalThesis | null
  risk: RiskAssessment | null
  bull_rebuttal: RebuttalSet | null
  bear_rebuttal: RebuttalSet | null
  memo: ResearchMemo | null
  verification: VerificationReport | null
  sources: SourceRef[]
  suggested_questions?: string[]
}

export interface DeskEvent {
  event_id?: string
  run_id: string
  type: string
  agent: string | null
  ts: string
  data: Record<string, any>
}

export interface HistoryRow {
  run_id: string
  created_at: string | null
  ticker: string | null
  company_name: string | null
  horizon: string | null
  stage: string | null
  confidence: string | null
  verification_verdict: string | null
  claims_flagged: number | null
  elapsed_s: number | null
}

export interface TickerCandidate {
  ticker: string
  name: string
  exchange: string | null
  kind: string | null
  score: number
  reason: string
}

export interface Resolution {
  query: string
  resolved: boolean
  ticker: string | null
  name: string | null
  needs_confirmation: boolean
  candidates: TickerCandidate[]
  message: string
}

// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * API helpers for the Cost Match module.
 *
 * Backed by /api/v1/cost-match/ — see
 * backend/app/modules/cost_match/router.py
 *
 * Money, quantities and confidence arrive as plain-decimal **strings**. The
 * backend is explicit about why (schemas.py): a unit rate crossing the wire as
 * a JSON number loses precision in every JavaScript client, and a confidence
 * that becomes a float turns a "0.75" boundary into 0.7499999999999999 in a
 * client-side comparison. They stay strings here and are parsed only where one
 * is compared, in costMatchStatus.ts.
 *
 * Enum-shaped response fields are typed `string`, not a union. The backend
 * declares them as bare `str` so a reader never crashes on a value a later
 * version writes; the unions below type what may be *sent*. Narrowing an
 * arriving value is `resultTier` / `decisionStateOf`'s job.
 *
 * The last two calls read the *costs* module rather than this one. They are
 * here rather than imported from `features/costs` because an override target
 * has to be scoped to the exact base the run is pinned to — cost_source plus
 * region plus catalog_id — and `GET /v1/costs/` is the only endpoint that
 * accepts all three together.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from '@/shared/lib/api';

/* ── Types ─────────────────────────────────────────────────────────────── */

/** Tier filter accepted by the results endpoint. Mirrors models.TIERS. */
export type TierFilter = 'exact' | 'high_confidence' | 'needs_review' | 'unmatched';

/** Decision-state filter accepted by the results endpoint. */
export type DecisionStateFilter = 'pending' | 'confirmed' | 'overridden' | 'rejected' | 'manual';

/** What a person may rule. `pending` is a state, never a ruling. */
export type DecisionKind = 'confirmed' | 'overridden' | 'rejected' | 'manual';

/** Run status. `closed` refuses further rulings until it is re-opened. */
export type RunStatus = 'matched' | 'closed';

/**
 * The cap the backend puts on one submission (schemas.MAX_BATCH_LINES).
 *
 * Past it a paste stops being a review queue and becomes a data migration. A
 * request carrying more is rejected whole, so the composer caps and says so
 * rather than letting 501 lines fail as one 422.
 */
export const MAX_BATCH_LINES = 500;

/**
 * One free-text line from the foreign bill.
 *
 * `description` may be blank on purpose: a pasted bill really does contain
 * header-only and empty rows, and dropping them here would hide them from both
 * the reviewer and the `cost_match.source_description_present` rule that exists
 * to report them.
 */
export interface MatchLineInput {
  description: string;
  unit?: string;
  /** Decimal string, or null when the bill carried no readable quantity. */
  quantity?: string | null;
  /** The subcontractor's own item code, when their bill carried one. */
  source_ref?: string;
}

export interface MatchRunCreateBody {
  project_id: string;
  name?: string;
  source_label?: string;
  /** Language the descriptions are scored in. Not the reader's language. */
  source_locale?: string;
  cost_source?: string;
  region?: string | null;
  catalog_id?: string | null;
  candidate_limit?: number;
  notes?: string | null;
  lines: MatchLineInput[];
}

/** Only the reviewer-editable metadata. The pinned base is not patchable. */
export interface MatchRunUpdateBody {
  name?: string;
  source_label?: string;
  status?: RunStatus;
  notes?: string | null;
}

/**
 * Aggregated state of one run.
 *
 * Computed from the results in SQL on every read and never stored, so a badge
 * here cannot disagree with the queue it describes. `queue_length` is the
 * pending half of the two tiers where the machine is explicitly not claiming an
 * answer.
 */
export interface MatchRunCounts {
  total: number;
  exact: number;
  high_confidence: number;
  needs_review: number;
  unmatched: number;
  pending: number;
  confirmed: number;
  overridden: number;
  rejected: number;
  manual: number;
  queue_length: number;
}

export interface MatchRun {
  id: string;
  project_id: string;
  name: string;
  source_label: string;
  source_locale: string;
  cost_source: string;
  region: string | null;
  catalog_id: string | null;
  status: string;
  /** How many lines were submitted. Immutable, unlike every count above. */
  item_count: number;
  candidate_limit: number;
  created_by: string | null;
  tenant_id: string | null;
  notes: string | null;
  counts: MatchRunCounts;
  created_at: string;
  updated_at: string;
}

/**
 * A runner-up cost item kept on the result so an override is a pick.
 *
 * `band` is the matcher's own traffic light for this candidate, already
 * resolved server-side. Render it; do not re-threshold `confidence`.
 */
export interface MatchCandidate {
  cost_item_id: string | null;
  code: string;
  description: string;
  unit: string;
  rate: string | null;
  currency: string;
  confidence: string;
  band: string;
  reason_codes: string[];
  /**
   * Canonical tokens that bridged the line and this candidate — the
   * evidence the reviewer's ruling teaches from. Optional because results
   * stored before token evidence was persisted carry no overlay.
   */
  matched_tokens?: string[];
  /** Line tokens this candidate never answered. */
  unmatched_tokens?: string[];
  /** True when this row contributed its rate to a pooled median. */
  in_pool?: boolean;
}

/** One ruling from the append-only history of a result. */
export interface MatchDecision {
  id: string;
  result_id: string;
  run_id: string;
  /** Per-result counter assigned by the service. The ordering to trust. */
  seq: number;
  decision: string;
  /** What the machine was showing when the person ruled, frozen here. */
  tier_at_decision: string;
  confidence_at_decision: string;
  decided_cost_item_id: string | null;
  decided_code: string;
  decided_description: string;
  decided_unit: string;
  decided_rate: string | null;
  decided_currency: string;
  decided_by: string | null;
  note: string | null;
  created_at: string;
}

export interface MatchResult {
  id: string;
  run_id: string;
  project_id: string;
  line_no: number;
  source_ref: string;
  source_description: string;
  source_unit: string;
  source_quantity: string | null;
  /** The automatic pass. Never moves after the run. */
  tier: string;
  confidence: string;
  /** A second candidate scored identically; the winner was picked by input
   *  order, which is not a judgement, so the screen has to say so. */
  tie: boolean;
  hint_code: string;
  /** Matcher vocabulary (`exact_match`, `unit_mismatch`). Never rendered raw —
   *  `explanation` is the same thing as a sentence in the reader's language. */
  reason_codes: string[];
  factors: Record<string, number>;
  alternatives: MatchCandidate[];
  suggested_cost_item_id: string | null;
  suggested_code: string;
  suggested_description: string;
  suggested_unit: string;
  suggested_rate: string | null;
  suggested_currency: string;
  /** The human pass. The only field a review moves. */
  decision_state: string;
  decisions: MatchDecision[];
  /** The AI web-verify record for this line's signature, when one exists.
   *  A lead to rule on — never a price that was applied. */
  web_estimate?: WebEstimate | null;
  /** Rendered from `reason_codes` in the locale the read asked for. */
  explanation: string;
  /** Why there is nothing to offer, rendered the same way. */
  hint: string | null;
  created_at: string;
  updated_at: string;
}

export interface MatchResultPage {
  run_id: string;
  total: number;
  offset: number;
  limit: number;
  items: MatchResult[];
}

/**
 * One AI-extracted market estimate for a line's signature.
 *
 * Advisory by construction — `source` is always `ai_estimate` (generated,
 * not observed). `price_median` only becomes money on the bill through a
 * `manual` ruling a person records. `eligible: false` means the line was
 * too project-specific for a market figure and stays unresolved.
 */
export interface WebEstimate {
  id: string;
  signature: string;
  result_id: string | null;
  description: string;
  unit: string;
  eligible: boolean;
  family_words: string[];
  price_min: string | null;
  price_max: string | null;
  price_median: string | null;
  currency: string;
  breakdown: { component?: string; label?: string; min?: number; max?: number }[];
  variants: { modifier?: string[]; price_delta?: number[]; note?: string }[];
  ai_confidence: string;
  reason: string;
  model: string;
  status: string;
  sources: { title?: string; url?: string; snippet?: string }[];
  source: string;
  created_at: string;
}

/**
 * A person's ruling.
 *
 * `confirmed` adopts the suggestion as it stands and must not name an item.
 * `overridden` must name one. `rejected` records that nothing in this base
 * fits, which is a real answer, and must not name one either. `manual` is
 * the same human pass with a price the reviewer supplies — `rate` (and
 * optionally `currency`) instead of a corpus item.
 */
export interface MatchDecisionBody {
  decision: DecisionKind;
  cost_item_id?: string | null;
  note?: string | null;
  rate?: string | null;
  currency?: string | null;
}

export interface CostMatchFinding {
  rule_id: string;
  severity: string;
  category: string;
  message: string;
  key: string;
  element_ref: string | null;
  suggestion: string | null;
  context: Record<string, unknown>;
}

export interface CostMatchValidationReport {
  target_type: string;
  target_id: string | null;
  status: string;
  error_count: number;
  warning_count: number;
  info_count: number;
  passed_count: number;
  findings: CostMatchFinding[];
  unsupported_rule_sets: string[];
}

/** One row of the cost base, as the costs module's search returns it. */
export interface CostBaseItem {
  id: string;
  code: string;
  description: string;
  unit: string;
  /** Declared `number` by the costs schema, delivered as a decimal string. */
  rate: string | number | null;
  currency?: string;
}

/* ── Calls ─────────────────────────────────────────────────────────────── */

const BASE = '/v1/cost-match';

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : '';
}

/**
 * Match a pasted batch against one cost base.
 *
 * Scored in the same round trip. Nothing is applied: every line comes back
 * pending and waits for a person, whatever tier it landed in.
 */
export function createRun(body: MatchRunCreateBody): Promise<MatchRun> {
  return apiPost<MatchRun, MatchRunCreateBody>(`${BASE}/runs/`, body);
}

export interface BoqMatchRunBody {
  source_locale?: string;
  cost_source?: string;
  /** Ordered base cascade - each base only receives positions the earlier
   * bases left unpriced. Overrides cost_source when present. */
  bases?: string[];
  region?: string | null;
  catalog_id?: string | null;
  candidate_limit?: number;
}

export interface BoqMatchRunResponse {
  boq_id: string;
  run_ids: string[];
  lines: number;
  counts: MatchRunCounts;
  positions_priced: number;
  positions_unpriced: number;
}

/**
 * The one-click path: match a whole BOQ against the cost base and write every
 * suggestion back onto its position with the evidence tier as `price_basis`.
 * Lines with no offer stay unresolved. Reviewer rulings re-sync the position.
 */
export function runBoqMatch(boqId: string, body: BoqMatchRunBody = {}): Promise<BoqMatchRunResponse> {
  return apiPost<BoqMatchRunResponse, BoqMatchRunBody>(`${BASE}/boq/${boqId}/run`, body);
}

export interface BoqMatchJob {
  job_id: string;
  boq_id: string;
  status: 'running' | 'done' | 'failed';
  started_at: string;
  finished_at?: string | null;
  lines_done?: number | null;
  lines_total?: number | null;
  result?: BoqMatchRunResponse | null;
  error?: string | null;
}

export function startBoqMatchJob(boqId: string, body: BoqMatchRunBody = {}): Promise<BoqMatchJob> {
  return apiPost<BoqMatchJob, BoqMatchRunBody>(`${BASE}/boq/${boqId}/run-async`, body);
}

export function getBoqMatchJob(boqId: string, jobId: string): Promise<BoqMatchJob> {
  return apiGet<BoqMatchJob>(`${BASE}/boq/${boqId}/run-jobs/${jobId}`);
}

/** The running job for this BOQ, or null - 404 means nothing is running. */
export async function getActiveBoqMatchJob(boqId: string): Promise<BoqMatchJob | null> {
  try {
    return await apiGet<BoqMatchJob>(`${BASE}/boq/${boqId}/run-jobs/active`);
  } catch {
    return null;
  }
}

/**
 * Poll one match job to completion. `onProgress` fires on every poll with the
 * latest job state so callers can render `lines_done / lines_total`.
 */
export async function pollBoqMatchJob(
  boqId: string,
  jobId: string,
  {
    pollMs = 3000,
    timeoutMs = 45 * 60 * 1000,
    onProgress,
  }: { pollMs?: number; timeoutMs?: number; onProgress?: (job: BoqMatchJob) => void } = {},
): Promise<BoqMatchRunResponse> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    await new Promise((r) => setTimeout(r, pollMs));
    const state = await getBoqMatchJob(boqId, jobId);
    onProgress?.(state);
    if (state.status === 'done' && state.result) return state.result;
    if (state.status === 'failed') throw new Error(state.error || 'Corpus match failed');
    if (Date.now() > deadline) throw new Error('Corpus match is still running - check the review queue');
  }
}

/**
 * The one-click path off the request clock: a large bill takes minutes to
 * match, longer than any proxy/browser timeout, so the server runs it as a
 * job and this resolves with the same payload the sync endpoint returned.
 * A repeat start while a job for the BOQ is running returns that same job,
 * so a retry after a dead request re-attaches instead of duplicating work.
 */
export async function runBoqMatchAsync(
  boqId: string,
  body: BoqMatchRunBody = {},
  opts: { pollMs?: number; timeoutMs?: number; onProgress?: (job: BoqMatchJob) => void } = {},
): Promise<BoqMatchRunResponse> {
  const job = await startBoqMatchJob(boqId, body);
  return pollBoqMatchJob(boqId, job.job_id, opts);
}

export interface BoqConfirmResponse {
  boq_id: string;
  confirmed: number;
  skipped: number;
  learned: number;
}

/**
 * Bulk-confirm the machine's priced suggestions on a bill.
 *
 * `positionIds` undefined confirms every still-pending result across the
 * bill's match runs ("confirm all"); a list scopes it to the selected rows.
 * Each row goes through the same decision flow the review queue uses, so
 * rulings land in the append-only history and positions flip to
 * `corpus_confirmed`. Confirmed prices are also learned into the corpus as
 * `estimate_confirmed` items - company evidence, kept by default.
 */
/* ── Web-verify (AI-extracted estimates) ───────────────────────────────── */

export interface WebVerifyJob {
  job_id: string;
  run_id: string;
  status: 'running' | 'done' | 'failed';
  started_at: string;
  finished_at?: string | null;
  lines_done?: number | null;
  lines_total?: number | null;
  result?: {
    run_id: string;
    lines_seen: number;
    unique_lines: number;
    estimates_written: number;
    estimates_reused: number;
    failed: number;
  } | null;
  error?: string | null;
}

/**
 * Extract AI market estimates for a run's lines, as a background job.
 *
 * `scope: 'queue'` covers the review queue; `'all'` re-checks priced lines
 * too — the same estimate doubles as a discrepancy flag against their
 * current rate. One call per unique line; the job handle is what the caller
 * polls for progress. A repeat start while a job is running returns it.
 */
export function startWebVerifyJob(
  runId: string,
  scope: 'queue' | 'all' = 'queue',
): Promise<WebVerifyJob> {
  return apiPost<WebVerifyJob, { scope: string }>(
    `${BASE}/runs/${runId}/web-verify`,
    { scope },
  );
}

export function getWebVerifyJob(runId: string, jobId: string): Promise<WebVerifyJob> {
  return apiGet<WebVerifyJob>(`${BASE}/runs/${runId}/web-verify-jobs/${jobId}`);
}

/** The running verify job for this run, or null - 404 means nothing running. */
export async function getActiveWebVerifyJob(runId: string): Promise<WebVerifyJob | null> {
  try {
    return await apiGet<WebVerifyJob>(`${BASE}/runs/${runId}/web-verify-jobs/active`);
  } catch {
    return null;
  }
}

/**
 * Poll one verify job to completion. `onProgress` fires on every poll with
 * the latest job state so callers can render `lines_done / lines_total`.
 * Long timeout: one LLM call per unique line is slow by nature.
 */
export async function pollWebVerifyJob(
  runId: string,
  jobId: string,
  {
    pollMs = 3000,
    timeoutMs = 90 * 60 * 1000,
    onProgress,
  }: { pollMs?: number; timeoutMs?: number; onProgress?: (job: WebVerifyJob) => void } = {},
): Promise<WebVerifyJob['result']> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    await new Promise((r) => setTimeout(r, pollMs));
    const state = await getWebVerifyJob(runId, jobId);
    onProgress?.(state);
    if (state.status === 'done') return state.result;
    if (state.status === 'failed') throw new Error(state.error || 'Web verify failed');
    if (Date.now() > deadline) throw new Error('Web verify is still running - check back shortly');
  }
}

export function confirmBoqPositions(
  boqId: string,
  positionIds?: string[],
): Promise<BoqConfirmResponse> {
  return apiPost<BoqConfirmResponse, { position_ids: string[] | null }>(
    `${BASE}/boq/${boqId}/confirm`,
    { position_ids: positionIds ?? null },
  );
}

/** Active cost items in the corpus - the banner's "what you're matching against" line. */
export async function fetchCorpusItemCount(): Promise<number | null> {
  try {
    const res = await apiGet<{ total?: number | null }>('/v1/costs/?limit=1');
    return typeof res.total === 'number' ? res.total : null;
  } catch {
    return null;
  }
}

/** Runs on one project, newest first, each with its live counts. */
export function listRuns(params: {
  projectId: string;
  status?: RunStatus;
  offset?: number;
  limit?: number;
}): Promise<MatchRun[]> {
  return apiGet<MatchRun[]>(
    `${BASE}/runs/${qs({
      project_id: params.projectId,
      status: params.status,
      offset: params.offset,
      limit: params.limit,
    })}`,
  );
}

export function getRun(runId: string): Promise<MatchRun> {
  return apiGet<MatchRun>(`${BASE}/runs/${runId}`);
}

/** Rename a run, re-label its source, or open and close its review. */
export function updateRun(runId: string, body: MatchRunUpdateBody): Promise<MatchRun> {
  return apiPatch<MatchRun, MatchRunUpdateBody>(`${BASE}/runs/${runId}`, body);
}

/** Delete a run with its results and their whole ruling history. */
export function deleteRun(runId: string): Promise<void> {
  return apiDelete<void>(`${BASE}/runs/${runId}`);
}

/**
 * One page of a run's results in submission order.
 *
 * `locale` is the *reader's* language: it decides what language `explanation`
 * and `hint` come back in, and defaults to English server-side. It is not
 * `run.source_locale`, which is the language the descriptions were scored in.
 */
export function listRunResults(
  runId: string,
  params: {
    tier?: TierFilter;
    decisionState?: DecisionStateFilter;
    locale?: string;
    offset?: number;
    limit?: number;
  } = {},
): Promise<MatchResultPage> {
  return apiGet<MatchResultPage>(
    `${BASE}/runs/${runId}/results${qs({
      tier: params.tier,
      decision_state: params.decisionState,
      locale: params.locale,
      offset: params.offset,
      limit: params.limit,
    })}`,
  );
}

/**
 * The lines that still need a person before anything can be priced.
 *
 * Narrower than "everything pending": the two tiers where the machine is
 * explicitly not claiming an answer. Exact and confident lines still need
 * confirming, and are asked for with `decisionState: 'pending'` instead.
 */
export function listReviewQueue(
  runId: string,
  params: { locale?: string; offset?: number; limit?: number } = {},
): Promise<MatchResultPage> {
  return apiGet<MatchResultPage>(
    `${BASE}/runs/${runId}/review-queue${qs({
      locale: params.locale,
      offset: params.offset,
      limit: params.limit,
    })}`,
  );
}

/**
 * Confirm, override or reject one suggested match.
 *
 * The only way a suggestion becomes something the project uses. Appended to the
 * line's history rather than replacing it, so a change of mind stays visible.
 */
export function decideResult(resultId: string, body: MatchDecisionBody): Promise<MatchDecision> {
  return apiPost<MatchDecision, MatchDecisionBody>(`${BASE}/results/${resultId}/decision`, body);
}

/** The `cost_match` rule set over a whole run, both scopes merged. */
export function validateRun(runId: string, locale?: string): Promise<CostMatchValidationReport> {
  return apiPost<CostMatchValidationReport>(`${BASE}/runs/${runId}/validate${qs({ locale })}`);
}

/* ── Cost base reads (costs module) ────────────────────────────────────── */

/** Regions that actually have cost items loaded, for pinning a run's base. */
export function listCostBaseRegions(): Promise<string[]> {
  return apiGet<string[]>('/v1/costs/regions/');
}

/**
 * Search the cost base a run is pinned to, for an override target.
 *
 * Scoped by all three of the run's pinning fields, because the decision
 * endpoint accepts only an active item of that exact base and answers 404 for
 * anything else. `lite` strips the per-row component array, which on a CWICR
 * row is tens of kilobytes this picker never reads.
 */
export function searchCostBase(params: {
  q: string;
  costSource?: string;
  region?: string | null;
  catalogId?: string | null;
  locale?: string;
  limit?: number;
}): Promise<{ items: CostBaseItem[] }> {
  return apiGet<{ items: CostBaseItem[] }>(
    `/v1/costs/${qs({
      q: params.q,
      source: params.costSource,
      region: params.region,
      catalog_id: params.catalogId,
      locale: params.locale,
      limit: params.limit ?? 20,
      lite: true,
    })}`,
  );
}

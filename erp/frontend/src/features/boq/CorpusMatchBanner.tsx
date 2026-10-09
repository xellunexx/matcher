// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * One-click corpus pricing for a BOQ.
 *
 * The flow the whole product is sold on: upload a bill, press one button,
 * get an estimate populated from the local cost corpus. The backend runs
 * the match as a job - a large bill takes minutes, longer than any proxy
 * timeout - so this component starts it, polls it, and can re-attach to a
 * job already running when the page was reloaded mid-match. Behind the
 * button the ordinary cost-match pipeline (chunked runs, tiers, review
 * queue) writes each suggestion onto its position with the evidence tier
 * as ``price_basis`` - so the price carries how strong its evidence is,
 * and lines with no offer stay unpriced rather than guessed.
 *
 * A ruling recorded later in Cost Match re-syncs the position, which is why
 * the summary toast points the reviewer at the queue.
 */
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckCheck, Coins, Loader2 } from 'lucide-react';


import { Button, ConfirmDialog } from '@/shared/ui';
import { useToastStore } from '@/stores/useToastStore';
import {
  confirmBoqPositions,
  fetchCorpusItemCount,
  getActiveBoqMatchJob,
  listRuns,
  pollBoqMatchJob,
  startBoqMatchJob,
  type BoqMatchRunResponse,
} from '@/features/cost-match/api';

/** Ordered base cascade offered in the match dialog - mirrors
 * ``cost_match.bases.BASE_CHOICES`` on the backend. Checked bases run in
 * this order, each stage receiving only positions the earlier stages left
 * unpriced. */
const COST_BASE_CHOICES = [
  { key: 'sek', label: 'SEK / Buildly', i18nKey: 'boq.corpus_base_sek' },
  { key: 'opr', label: 'Operator price files', i18nKey: 'boq.corpus_base_opr' },
  { key: 'operator', label: 'Operator', i18nKey: 'boq.corpus_base_operator' },
  { key: 'web', label: 'Web sources', i18nKey: 'boq.corpus_base_web' },
  { key: 'cwicr', label: 'CWICR corpus', i18nKey: 'boq.corpus_base_cwicr' },
  { key: 'tenders', label: 'Won tenders', i18nKey: 'boq.corpus_base_tenders' },
  { key: 'learned', label: 'Learned (confirmed)', i18nKey: 'boq.corpus_base_learned' },
  { key: 'labor', label: 'Labour list', i18nKey: 'boq.corpus_base_labor' },
] as const;

interface CorpusMatchBannerProps {
  boqId: string;
  projectId?: string;
  disabled?: boolean;
  /** Currently selected grid rows - enables "Confirm selected". */
  selectedPositionIds?: string[];
  /** Priced-by-machine rows still awaiting a ruling - enables "Confirm all". */
  unconfirmedCount?: number;
  onSelectionConsumed?: () => void;
}

export function CorpusMatchBanner({
  boqId,
  projectId,
  disabled,
  selectedPositionIds = [],
  unconfirmedCount = 0,
  onSelectionConsumed,
}: CorpusMatchBannerProps) {
  const { t } = useTranslation();
    const queryClient = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const [confirming, setConfirming] = useState(false);
  const [confirmingAll, setConfirmingAll] = useState(false);
  const [starting, setStarting] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  // The job this page is polling - set by a fresh start OR recovered from the
  // server on mount, so a reload mid-match re-attaches instead of looking dead.
  const [pollJobId, setPollJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ done: number | null; total: number | null }>({
    done: null,
    total: null,
  });
  const [doneResult, setDoneResult] = useState<BoqMatchRunResponse | null>(null);
  // Ordered base cascade: the checked databases are tried in the listed
  // order, and each stage only sees the positions the earlier stages left
  // unpriced. Nothing checked = the whole corpus, exactly as before.
  const [selectedBases, setSelectedBases] = useState<string[]>([]);

  const corpusQuery = useQuery({
    queryKey: ['corpus-item-count'],
    queryFn: fetchCorpusItemCount,
    staleTime: 60_000,
  });

  // On mount: is a match already running for this BOQ? If so, resume polling.
  const activeJobQuery = useQuery({
    queryKey: ['boq-match-active-job', boqId],
    queryFn: () => getActiveBoqMatchJob(boqId),
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    const job = activeJobQuery.data;
    if (job && job.status === 'running' && pollJobId === null) {
      setPollJobId(job.job_id);
      setProgress({ done: job.lines_done ?? null, total: job.lines_total ?? null });
    }
  }, [activeJobQuery.data, pollJobId]);

  // The review-queue button must not depend on a match finishing while this
  // page was open: a run raised earlier (or before a reload/restart) lives in
  // the database either way, and the queue is how its rulings are reached.
  // Runs raised from a BOQ carry {"boq_id": ...} in notes - match on that.
  const existingRunsQuery = useQuery({
    queryKey: ['cost-match-runs', projectId],
    queryFn: () => listRuns({ projectId: projectId as string, limit: 50 }),
    enabled: Boolean(projectId),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const hasRunForThisBoq = (existingRunsQuery.data ?? []).some((run) => {
    try {
      const notes = JSON.parse(run.notes ?? '');
      return notes?.boq_id === boqId;
    } catch {
      return false;
    }
  });

  const finishWithResult = (res: BoqMatchRunResponse) => {
    setDoneResult(res);
    queryClient.invalidateQueries({ queryKey: ['boq', boqId] });
    queryClient.invalidateQueries({ queryKey: ['boq-cost-breakdown', boqId] });
    queryClient.invalidateQueries({ queryKey: ['boq-resource-summary', boqId] });
    queryClient.invalidateQueries({ queryKey: ['cost-match'] });
    addToast({
      type: 'success',
      title: t('boq.corpus_match_done_title', { defaultValue: 'Bill priced from the corpus' }),
      message: t('boq.corpus_match_done', {
        defaultValue:
          '{{priced}} of {{lines}} positions priced ({{exact}} exact, {{high}} confident, {{review}} to review). {{unpriced}} lines have no corpus evidence and stay unpriced. The review queue holds every ruling.',
        priced: res.positions_priced,
        lines: res.lines,
        exact: res.counts.exact,
        high: res.counts.high_confidence,
        review: res.counts.needs_review,
        unpriced: res.positions_unpriced,
      }),
    });
  };

  const failWithError = (error: unknown) => {
    addToast({
      type: 'error',
      title: t('boq.corpus_match_failed', { defaultValue: 'Corpus match failed' }),
      message:
        error instanceof Error && error.message
          ? error.message
          : t('boq.corpus_match_failed_detail', {
              defaultValue: 'The match run could not finish. Try again - already priced lines keep their values.',
            }),
    });
  };

  // The single polling loop - both a fresh start and a reloaded-page
  // re-attach end up here.
  useEffect(() => {
    if (!pollJobId) return;
    let cancelled = false;
    pollBoqMatchJob(boqId, pollJobId, {
      onProgress: (job) => {
        if (!cancelled) setProgress({ done: job.lines_done ?? null, total: job.lines_total ?? null });
      },
    })
      .then((res) => {
        if (cancelled) return;
        setPollJobId(null);
        finishWithResult(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setPollJobId(null);
        failWithError(err);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollJobId, boqId]);

  const startMatch = async () => {
    setStarting(true);
    try {
      const job = await startBoqMatchJob(
        boqId,
        selectedBases.length ? { bases: selectedBases } : {},
      );
      setDoneResult(null);
      setProgress({ done: job.lines_done ?? null, total: job.lines_total ?? null });
      setPollJobId(job.job_id);
    } catch (err) {
      failWithError(err);
    } finally {
      setStarting(false);
      setConfirming(false);
    }
  };

  const running = starting || pollJobId !== null;
  const corpusCount = corpusQuery.data;

  // Bulk confirm: every priced-but-unruled row stays in the "Needs review"
  // filter until a person confirms it here or in the review queue. The
  // ruling goes through the same decision flow either way - the only
  // difference is how many rows one click covers.
  const runConfirm = async (positionIds?: string[]) => {
    setConfirmBusy(true);
    try {
      const res = await confirmBoqPositions(boqId, positionIds);
      queryClient.invalidateQueries({ queryKey: ['boq', boqId] });
      queryClient.invalidateQueries({ queryKey: ['boq-cost-breakdown', boqId] });
      queryClient.invalidateQueries({ queryKey: ['cost-match'] });
      addToast({
        type: 'success',
        title: t('boq.corpus_confirm_done', {
          defaultValue: '{{confirmed}} positions confirmed{{learned}}',
          confirmed: res.confirmed,
          learned:
            res.learned > 0
              ? t('boq.corpus_confirm_learned', {
                  defaultValue: ' - {{n}} added to the corpus',
                  n: res.learned,
                })
              : '',
        }),
        message:
          res.skipped > 0
            ? t('boq.corpus_confirm_skipped', {
                defaultValue: '{{skipped}} rows had nothing pending to confirm',
                skipped: res.skipped,
              })
            : undefined,
      });
      if (positionIds) onSelectionConsumed?.();
    } catch (err) {
      addToast({
        type: 'error',
        title: t('boq.corpus_confirm_failed', { defaultValue: 'Confirm failed' }),
        message: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setConfirmBusy(false);
      setConfirmingAll(false);
    }
  };

  return (
    <div
      className="mb-2 flex items-center gap-3 rounded-lg border border-border-light bg-surface-elevated px-3 py-2"
      data-testid="corpus-match-banner"
    >
      <Coins size={16} className="text-oe-blue shrink-0" />
      <span className="text-sm text-content-secondary flex-1 min-w-0 truncate">
        {t('boq.corpus_match_hint', {
          defaultValue: 'Price this bill against the local cost corpus.',
        })}
        {corpusCount != null && (
          <span className="text-content-tertiary">
            {' '}
            {t('boq.corpus_match_corpus', {
              defaultValue: '(corpus: {{count}} prices)',
              count: corpusCount.toLocaleString(),
            })}
          </span>
        )}
      </span>
      {running && (
        <span className="text-xs text-content-tertiary inline-flex items-center gap-1">
          <Loader2 size={12} className="animate-spin" />
          {progress.done != null && progress.total != null
            ? t('boq.corpus_match_running_progress', {
                defaultValue: 'Matching - {{done}} of {{total}} lines',
                done: progress.done,
                total: progress.total,
              })
            : t('boq.corpus_match_running', { defaultValue: 'Matching - can take a few minutes' })}
        </span>
      )}
      {selectedPositionIds.length > 0 && (
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || confirmBusy}
          onClick={() => runConfirm(selectedPositionIds)}
          data-testid="corpus-confirm-selected"
        >
          <CheckCheck size={13} />
          {t('boq.corpus_confirm_selected', {
            defaultValue: 'Confirm selected ({{n}})',
            n: selectedPositionIds.length,
          })}
        </Button>
      )}
      {unconfirmedCount > 0 && (
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || confirmBusy}
          onClick={() => setConfirmingAll(true)}
          data-testid="corpus-confirm-all"
        >
          {t('boq.corpus_confirm_all', {
            defaultValue: 'Confirm all ({{n}})',
            n: unconfirmedCount,
          })}
        </Button>
      )}
      <Button
        size="sm"
        variant="primary"
        disabled={disabled || running}
        onClick={() => setConfirming(true)}
        data-testid="corpus-match-button"
      >
        {t('boq.corpus_match_button', { defaultValue: 'Match against corpus' })}
      </Button>
      {(doneResult || hasRunForThisBoq) && (
        <Button
          size="sm"
          variant="ghost"
          to={'/cost-match'}
          data-testid="corpus-match-review"
        >
          {t('boq.corpus_match_review', { defaultValue: 'Open review queue' })}
        </Button>
      )}
      <ConfirmDialog
        open={confirming}
        variant="warning"
        title={t('boq.corpus_match_title', { defaultValue: 'Price this bill from the corpus?' })}
        message={t('boq.corpus_match_confirm', {
          defaultValue:
            'Every priceable position is matched against the local cost corpus. Suggestions are written onto the positions immediately with their evidence tier, lines without evidence stay unpriced, and every line lands in the review queue for a person. This can take a few minutes on a large bill.',
        })}
        confirmLabel={t('boq.corpus_match_go', { defaultValue: 'Match & price' })}
        onConfirm={startMatch}
        onCancel={() => setConfirming(false)}
        loading={starting}
      >
        <div className="mt-3 text-left">
          <p className="text-xs text-content-tertiary mb-1.5">
            {t('boq.corpus_match_bases_hint', {
              defaultValue:
                'Limit the bases - checked ones run in this order, each on the lines the previous left unpriced:',
            })}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {COST_BASE_CHOICES.map((base) => {
              const active = selectedBases.includes(base.key);
              return (
                <button
                  key={base.key}
                  type="button"
                  onClick={() =>
                    setSelectedBases((prev) =>
                      active ? prev.filter((b) => b !== base.key) : [...prev, base.key],
                    )
                  }
                  className={[
                    'rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
                    active
                      ? 'border-oe-blue bg-oe-blue/15 text-oe-blue'
                      : 'border-border bg-surface-primary text-content-secondary hover:border-oe-blue/50',
                  ].join(' ')}
                  data-testid={`corpus-base-${base.key}`}
                >
                  {t(base.i18nKey, { defaultValue: base.label })}
                </button>
              );
            })}
          </div>
        </div>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmingAll}
        variant="warning"
        title={t('boq.corpus_confirm_all_title', { defaultValue: 'Confirm all suggestions?' })}
        message={t('boq.corpus_confirm_all_msg', {
          defaultValue:
            'Every position still holding an unruled corpus price gets a confirmed ruling by you and is added to the corpus as company evidence. Rows already confirmed, overridden or rejected are not touched.',
          n: unconfirmedCount,
        })}
        confirmLabel={t('boq.corpus_confirm_all_go', { defaultValue: 'Confirm all' })}
        onConfirm={() => runConfirm()}
        onCancel={() => setConfirmingAll(false)}
        loading={confirmBusy}
      />
    </div>
  );
}

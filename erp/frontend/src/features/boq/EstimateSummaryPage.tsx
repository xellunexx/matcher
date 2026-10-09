// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Estimate Summary — where the outcome of "Match against corpus" lives
 * after the success toast disappears.
 *
 * For every bill of the active project this page reads each position's
 * ``price_basis`` (the tier the matcher wrote, or the ruling a reviewer
 * recorded over it) and totals it into one bar per bill: how much of the
 * estimate stands on exact corpus evidence, how much on suggestions still
 * awaiting a ruling, and how much is unresolved. Unresolved means exactly
 * what the no-fabrication invariant promises: no corpus evidence reached
 * the row, so no price did either — the number is the buyer's remaining
 * work queue, not a hidden gap.
 *
 * Data source is the same ``boqApi.get`` payload the BOQ editor uses, so
 * the numbers here cannot drift from the numbers in the grid.
 */
import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { FileBarChart, ArrowRight } from 'lucide-react';

import { boqApi, type BOQListRow, type Position } from '@/features/boq/api';
import { isSection } from '@/features/boq/api';
import { useProjectContextStore } from '@/stores/useProjectContextStore';
import { formatCurrency, toNum } from '@/shared/lib/money';

/**
 * Buckets the page reports on. `match` = the matcher wrote the price;
 * `ruled` = a person confirmed/overrode it; `unresolved` = no usable
 * evidence (withheld suggestions and lines never priced). Order inside
 * each list is the order the stacked bar renders.
 */
const BUCKETS = [
  {
    key: 'corpus_exact',
    labelKey: 'estimate_summary.bucket_exact',
    label: 'Exact match',
    cls: 'bg-emerald-500',
    group: 'match' as const,
  },
  {
    key: 'corpus_high',
    labelKey: 'estimate_summary.bucket_high',
    label: 'Confident match',
    cls: 'bg-oe-blue',
    group: 'match' as const,
  },
  {
    key: 'corpus_reference',
    labelKey: 'estimate_summary.bucket_reference',
    label: 'Reference price',
    cls: 'bg-amber-400',
    group: 'match' as const,
  },
  {
    key: 'corpus_review',
    labelKey: 'estimate_summary.bucket_review',
    label: 'Suggestion to review',
    cls: 'bg-amber-500',
    group: 'match' as const,
  },
  {
    key: 'corpus_confirmed',
    labelKey: 'estimate_summary.bucket_confirmed',
    label: 'Confirmed',
    cls: 'bg-emerald-600',
    group: 'ruled' as const,
  },
  {
    key: 'corpus_override',
    labelKey: 'estimate_summary.bucket_override',
    label: 'Overridden',
    cls: 'bg-violet-500',
    group: 'ruled' as const,
  },
  {
    key: 'corpus_rejected',
    labelKey: 'estimate_summary.bucket_rejected',
    label: 'Rejected',
    cls: 'bg-rose-300',
    group: 'ruled' as const,
  },
  {
    key: 'corpus_unit_mismatch',
    labelKey: 'estimate_summary.bucket_unit',
    label: 'Unit mismatch',
    cls: 'bg-rose-500',
    group: 'unresolved' as const,
  },
  {
    key: 'corpus_currency_mismatch',
    labelKey: 'estimate_summary.bucket_fx',
    label: 'Currency mismatch',
    cls: 'bg-rose-500',
    group: 'unresolved' as const,
  },
  {
    key: 'corpus_no_match',
    labelKey: 'estimate_summary.bucket_nomatch',
    label: 'No corpus evidence',
    cls: 'bg-rose-400',
    group: 'unresolved' as const,
  },
  {
    key: 'unpriced',
    labelKey: 'estimate_summary.bucket_unpriced',
    label: 'Never priced',
    cls: 'bg-surface-quaternary',
    group: 'unresolved' as const,
  },
  {
    key: 'other',
    labelKey: 'estimate_summary.bucket_other',
    label: 'Manual / other',
    cls: 'bg-content-quaternary',
    group: 'ruled' as const,
  },
];

type BucketKey = (typeof BUCKETS)[number]['key'];

interface BoqSummary {
  boq: BOQListRow;
  leafCount: number;
  counts: Record<BucketKey, number>;
  unresolved: number;
  ruled: number;
}

function summarize(boq: BOQListRow, positions: Position[]): BoqSummary {
  const counts = Object.fromEntries(BUCKETS.map((b) => [b.key, 0])) as Record<BucketKey, number>;
  const bump = (k: BucketKey) => {
    counts[k] = (counts[k] ?? 0) + 1;
  };
  let leafCount = 0;
  for (const p of positions) {
    if (isSection(p)) continue;
    leafCount += 1;
    const basis = p.price_basis ?? null;
    const priced = p.unit_rate != null && toNum(p.unit_rate) !== 0;
    if (!priced && !basis) {
      bump('unpriced');
      continue;
    }
    const key = (basis ?? 'other') as BucketKey;
    bump(key in counts ? key : 'other');
  }
  const unresolved = BUCKETS.filter((b) => b.group === 'unresolved').reduce(
    (s, b) => s + (counts[b.key] ?? 0),
    0,
  );
  const ruled = BUCKETS.filter((b) => b.group === 'ruled').reduce(
    (s, b) => s + (counts[b.key] ?? 0),
    0,
  );
  return { boq, leafCount, counts, unresolved, ruled };
}

export function EstimateSummaryPage() {
  const { t } = useTranslation();
  const activeProjectId = useProjectContextStore((s) => s.activeProjectId);
  const activeProjectName = useProjectContextStore((s) => s.activeProjectName);

  const boqsQuery = useQuery({
    queryKey: ['estimate-summary-boqs', activeProjectId],
    enabled: Boolean(activeProjectId),
    queryFn: async () => {
      const byProject = await boqApi.listForProjects([activeProjectId!]);
      return byProject[activeProjectId!] ?? [];
    },
  });

  const boqs = boqsQuery.data ?? [];
  const detailQueries = useQueries({
    queries: boqs.map((b) => ({
      queryKey: ['boq', b.id],
      queryFn: () => boqApi.get(b.id),
      staleTime: 30_000,
    })),
  });

  const summaries = useMemo(() => {
    const out: BoqSummary[] = [];
    boqs.forEach((b, i) => {
      const detail = detailQueries[i]?.data;
      if (detail) out.push(summarize(b, detail.positions));
    });
    return out;
  }, [boqs, detailQueries]);

  const loading = boqsQuery.isLoading || detailQueries.some((q) => q.isLoading);

  if (!activeProjectId) {
    return (
      <div className="p-6 text-sm text-content-tertiary" data-testid="estimate-summary-empty">
        {t('estimate_summary.no_project', {
          defaultValue: 'Pick a project to see its estimate summary.',
        })}
      </div>
    );
  }

  return (
    <div className="p-4 max-w-5xl mx-auto" data-testid="estimate-summary-page">
      <div className="flex items-center gap-2 mb-1">
        <FileBarChart size={18} className="text-oe-blue" />
        <h1 className="text-lg font-semibold text-content-primary">
          {t('estimate_summary.title', { defaultValue: 'Estimate Summary' })}
        </h1>
      </div>
      <p className="text-xs text-content-tertiary mb-4">
        {t('estimate_summary.subtitle', {
          defaultValue:
            'How every line of {{project}} was priced — corpus tiers, reviewer rulings, and what is still unresolved.',
          project: activeProjectName,
        })}
      </p>

      {loading && (
        <div className="text-sm text-content-tertiary">
          {t('common.loading', { defaultValue: 'Loading…' })}
        </div>
      )}

      {!loading && summaries.length === 0 && (
        <div className="text-sm text-content-tertiary">
          {t('estimate_summary.no_boqs', { defaultValue: 'This project has no bills yet.' })}
        </div>
      )}

      <div className="space-y-4">
        {summaries.map((s) => (
          <div
            key={s.boq.id}
            className="rounded-lg border border-border-light bg-surface-elevated p-4"
            data-testid={`estimate-summary-boq-${s.boq.id}`}
          >
            <div className="flex items-baseline justify-between gap-3 mb-3">
              <div className="min-w-0">
                <Link
                  to={`/boq/${s.boq.id}`}
                  className="text-sm font-semibold text-content-primary hover:text-oe-blue truncate inline-flex items-center gap-1"
                >
                  {s.boq.name}
                  <ArrowRight size={13} />
                </Link>
                <div className="text-2xs text-content-tertiary">
                  {t('estimate_summary.lines', {
                    defaultValue: '{{count}} positions',
                    count: s.leafCount,
                  })}
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-semibold tabular-nums text-content-primary">
                  {formatCurrency(s.boq.grand_total ?? s.boq.direct_cost_total)}
                </div>
                <div className="text-2xs text-content-tertiary">
                  {s.unresolved > 0
                    ? t('estimate_summary.unresolved', {
                        defaultValue: '{{count}} unresolved',
                        count: s.unresolved,
                      })
                    : t('estimate_summary.all_resolved', { defaultValue: 'Fully priced' })}
                </div>
              </div>
            </div>

            {/* Stacked tier bar — each segment's width is its share of leaf
                positions, so the whole bar is the whole bill at a glance. */}
            <div className="flex h-3 rounded-full overflow-hidden bg-surface-secondary mb-3">
              {BUCKETS.map((b) =>
                (s.counts[b.key] ?? 0) > 0 ? (
                  <div
                    key={b.key}
                    className={`${b.cls} h-full`}
                    style={{
                      width: `${((s.counts[b.key] ?? 0) / Math.max(s.leafCount, 1)) * 100}%`,
                    }}
                    title={`${t(b.labelKey, { defaultValue: b.label })}: ${s.counts[b.key] ?? 0}`}
                  />
                ) : null,
              )}
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {BUCKETS.map((b) =>
                (s.counts[b.key] ?? 0) > 0 ? (
                  <span
                    key={b.key}
                    className="inline-flex items-center gap-1.5 text-2xs text-content-secondary"
                  >
                    <span className={`inline-block h-2 w-2 rounded-full ${b.cls}`} />
                    {t(b.labelKey, { defaultValue: b.label })}
                    <span className="tabular-nums font-medium">{s.counts[b.key] ?? 0}</span>
                  </span>
                ) : null,
              )}
            </div>

            {s.unresolved > 0 && (
              <div className="mt-3 flex items-center gap-3">
                <Link
                  to={`/boq/${s.boq.id}`}
                  className="text-2xs font-medium text-oe-blue hover:underline"
                >
                  {t('estimate_summary.open_bill', { defaultValue: 'Open bill to resolve' })}
                </Link>
                <Link
                  to="/cost-match"
                  className="text-2xs font-medium text-oe-blue hover:underline"
                >
                  {t('estimate_summary.open_review', { defaultValue: 'Open review queue' })}
                </Link>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

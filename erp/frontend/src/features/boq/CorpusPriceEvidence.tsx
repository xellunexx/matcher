import { Link } from 'react-router-dom';

interface EvidenceProps {
  data: {
    id?: string;
    boq_id?: string;
    price_basis?: string | null;
    metadata?: Record<string, unknown>;
  };
  t: (key: string, opts?: Record<string, string | number>) => string;
}

export function CorpusPriceEvidence({ data, t }: EvidenceProps) {
  if (!['corpus_review', 'corpus_unit_mismatch', 'corpus_currency_mismatch'].includes(data.price_basis ?? '')) return null;
  const raw = data.metadata?.cost_match;
  if (!raw || typeof raw !== 'object') return null;
  const offer = raw as Record<string, unknown>;
  const rate = Number(offer.suggested_rate);
  if (!Number.isFinite(rate) || rate <= 0) return null;
  const currency = typeof offer.currency === 'string' ? offer.currency : '';
  const unit = typeof offer.unit === 'string' ? offer.unit : '';
  const label = t('boq.corpus_offer', {
    defaultValue: 'Offer: {{rate}} {{currency}}/{{unit}}',
    rate: String(offer.suggested_rate), currency, unit,
  });
  const title = t('boq.corpus_offer_review', {
    defaultValue: '{{description}} — suggestion only, not included in totals. Open the match run to review.',
    description: typeof offer.description === 'string' ? offer.description : label,
  });
  const params = new URLSearchParams();
  if (data.boq_id) params.set('boq_id', data.boq_id);
  if (typeof offer.run_id === 'string') params.set('run_id', offer.run_id);
  if (typeof offer.result_id === 'string') params.set('result_id', offer.result_id);
  return (
    <Link to={`/cost-match?${params}`} title={title}
      onClick={(event) => event.stopPropagation()}
      className="truncate text-[10px] text-amber-700 dark:text-amber-300 underline"
      data-testid={`boq-corpus-offer-${data.id}`}>
      {label}
    </Link>
  );
}

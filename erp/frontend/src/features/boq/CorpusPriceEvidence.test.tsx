import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CorpusPriceEvidence } from './CorpusPriceEvidence';

const t = (_key: string, options?: Record<string, string | number>) =>
  String(options?.defaultValue ?? '').replace(/{{(\w+)}}/g, (_, key: string) => String(options?.[key] ?? ''));
const data = {
  id: 'position', boq_id: 'bill', price_basis: 'corpus_review', unit_rate: 0, total: 0,
  metadata: { cost_match: {
    run_id: 'run', result_id: 'result', suggested_rate: '5.575', currency: 'EUR', unit: 'м2',
    description: 'Боядисване с латекс',
  } },
};

describe('CorpusPriceEvidence', () => {
  it('shows the available quotation without changing the withheld rate or total', () => {
    render(<MemoryRouter><CorpusPriceEvidence data={data} t={t} /></MemoryRouter>);
    const link = screen.getByRole('link', { name: 'Offer: 5.575 EUR/м2' });
    expect(link).toHaveAttribute('href', '/cost-match?boq_id=bill&run_id=run&result_id=result');
    expect(link.title).toContain('not included in totals');
    expect(data.unit_rate).toBe(0);
    expect(data.total).toBe(0);
  });
  it.each(['corpus_unit_mismatch', 'corpus_currency_mismatch'])('does not hide %s evidence', (basis) => {
    render(<MemoryRouter><CorpusPriceEvidence data={{ ...data, price_basis: basis }} t={t} /></MemoryRouter>);
    expect(screen.getByRole('link')).toHaveTextContent('5.575 EUR/м2');
  });
  it.each(['corpus_confirmed', 'corpus_rejected', 'corpus_no_match'])('does not revive a %s suggestion', (basis) => {
    const { container } = render(<MemoryRouter><CorpusPriceEvidence data={{ ...data, price_basis: basis }} t={t} /></MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
  });
  it.each(['0', '-2', 'NaN', 'Infinity'])('does not present an unusable rate %s as a price', (rate) => {
    const altered = { ...data, metadata: { cost_match: { ...data.metadata.cost_match, suggested_rate: rate } } };
    const { container } = render(<MemoryRouter><CorpusPriceEvidence data={altered} t={t} /></MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
  });
});

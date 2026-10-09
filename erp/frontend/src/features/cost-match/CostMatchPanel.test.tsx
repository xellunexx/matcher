import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { useProjectContextStore } from '@/stores/useProjectContextStore';
import { CostMatchPanel } from './CostMatchPanel';
import { decideResult, getResult, getRun, listAllRuns, listReviewQueue, listRunResults, previewSemanticLinks, type MatchResult, type MatchRun } from './api';
import fixture from './priceContract.fixture.json';

vi.mock('react-i18next', () => ({ useTranslation: () => ({
  i18n: { language: 'bg' },
  t: (key: string, options?: Record<string, unknown>) =>
    String(options?.defaultValue ?? key).replace(/{{(\w+)}}/g, (_, name: string) => String(options?.[name] ?? '')),
}) }));
vi.mock('@/shared/lib/api', () => ({ getErrorMessage: (error: Error) => error.message }));
vi.mock('./api', () => ({
  MAX_BATCH_LINES: 500, createRun: vi.fn(), decideResult: vi.fn(), deleteRun: vi.fn(),
  getRun: vi.fn(), getResult: vi.fn(), listAllRuns: vi.fn(), listReviewQueue: vi.fn(), listRunResults: vi.fn(),
  listCostBaseRegions: vi.fn(), searchCostBase: vi.fn(), previewSemanticLinks: vi.fn(), startWebVerifyJob: vi.fn(), pollWebVerifyJob: vi.fn(),
  updateRun: vi.fn(), validateRun: vi.fn(),
}));

const run: MatchRun = {
  id: fixture.run_id, project_id: fixture.project_id, name: 'Target bill', source_label: 'KCC', source_locale: 'bg',
  cost_source: 'operator', region: null, catalog_id: null, status: 'matched', item_count: 51, candidate_limit: 50,
  created_by: null, tenant_id: null, notes: JSON.stringify({ boq_id: 'bill' }),
  counts: { total: 51, exact: 0, high_confidence: 0, needs_review: 51, unmatched: 0, pending: 51,
    confirmed: 0, overridden: 0, rejected: 0, manual: 0, queue_length: 51 },
  created_at: fixture.created_at, updated_at: fixture.updated_at,
};
let rows: MatchResult[];

beforeEach(() => {
  vi.clearAllMocks();
  useProjectContextStore.setState({ activeProjectId: 'wrong-global-project' });
  rows = Array.from({ length: 51 }, (_, index) => ({ ...fixture, id: `result-${index}`, line_no: index + 1, source_description: `Row ${index + 1}` }));
  vi.mocked(getRun).mockResolvedValue(run);
  vi.mocked(getResult).mockResolvedValue({ ...fixture, id: 'linked', source_description: 'Linked quotation' });
  vi.mocked(listAllRuns).mockResolvedValue([{ ...run, id: 'other-run', name: 'Other bill', notes: '{"boq_id":"other"}' }, run]);
  const page = async (_runId: string, params?: { offset?: number; limit?: number }) => ({
    run_id: run.id, total: rows.length, offset: params?.offset ?? 0, limit: params?.limit ?? 50,
    items: rows.slice(params?.offset ?? 0, (params?.offset ?? 0) + (params?.limit ?? 50)),
  });
  vi.mocked(listRunResults).mockImplementation(page);
  vi.mocked(listReviewQueue).mockImplementation(page);
});

function mount(props: Parameters<typeof CostMatchPanel>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const mounted = render(<MemoryRouter><QueryClientProvider client={client}>
    <CostMatchPanel boqId="bill" initialRunId={run.id} {...props} />
  </QueryClientProvider></MemoryRouter>);
  return { ...mounted, client };
}

describe('CostMatchPanel price visibility', () => {
  it('opens the requested bill/project, pages past row fifty, and resets on a tier change', async () => {
    mount({ initialTab: 'all' });
    await screen.findByText('Row 1');
    expect(listAllRuns).toHaveBeenCalledWith(fixture.project_id);
    expect(screen.queryByText('Other bill')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Row 51');
    expect(screen.getByText('5.575 EUR')).toBeInTheDocument();
    expect(listRunResults).toHaveBeenLastCalledWith(run.id, expect.objectContaining({ offset: 50 }));
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    const select = screen.getByRole('option', { name: 'Any tier' }).parentElement!;
    fireEvent.change(select, { target: { value: 'needs_review' } });
    await screen.findByText('Row 1');
    expect(listRunResults).toHaveBeenLastCalledWith(run.id, expect.objectContaining({ offset: 0, tier: 'needs_review' }));
  });
  it('moves back when a ruling empties the last queue page, and invalidates BOQ prices', async () => {
    const { client } = mount();
    client.setQueryData(['boq', 'bill'], { unit_rate: 0 });
    vi.mocked(decideResult).mockImplementation(async (id) => {
      rows = rows.filter((row) => row.id !== id);
      return {} as Awaited<ReturnType<typeof decideResult>>;
    });
    await screen.findByText('Row 1');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Row 51');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await screen.findByText('Row 1');
    await waitFor(() => expect(client.getQueryState(['boq', 'bill'])?.isInvalidated).toBe(true));
    expect(listReviewQueue).toHaveBeenLastCalledWith(run.id, expect.objectContaining({ offset: 0 }));
  });
  it('shows a directly linked quotation even when it is outside the current result page', async () => {
    mount({ initialTab: 'all', initialResultId: 'linked' });
    await screen.findByText('Linked quotation');
    expect(getResult).toHaveBeenCalledWith('linked', 'bg');
  });
  it('reports an API failure instead of pretending there are no candidates', async () => {
    vi.mocked(listReviewQueue).mockRejectedValue(new Error('Pricing service unavailable'));
    mount();
    await screen.findByRole('alert');
    expect(screen.getByRole('alert')).toHaveTextContent('Pricing service unavailable');
    expect(screen.queryByText(/Nothing here is waiting/)).not.toBeInTheDocument();
  });
});

describe('reviewing work links before adopting a quotation', () => {
  const link = { id: 'a'.repeat(24), role: 'action', label: 'Действие',
    query: 'Разваляне', candidate: 'Очукване' };

  beforeEach(() => {
    rows = [{ ...fixture, id: 'review-one', source_description: 'Разваляне на 25 см. тухлен зид',
      factors: { ...fixture.factors, semantic_links: [link] } }];
  });

  it('makes the two-sided relation clickable and sends only an expressly accepted link', async () => {
    mount();
    const accepted = await screen.findByRole('button', { name: /Действие: Разваляне.*Очукване.*същото/ });
    fireEvent.click(accepted);
    expect(accepted).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(decideResult).toHaveBeenCalledWith('review-one', expect.objectContaining({
      decision: 'confirmed', semantic_choices: [{ link_id: link.id, verdict: 'same' }],
    })));
  });

  it('can undo a link; confirming a price alone does not teach a semantic rule', async () => {
    mount();
    const accepted = await screen.findByRole('button', { name: /Действие: Разваляне.*Очукване.*същото/ });
    fireEvent.click(accepted);
    fireEvent.click(accepted);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(decideResult).toHaveBeenCalledWith('review-one', expect.not.objectContaining({
      semantic_choices: expect.anything(),
    })));
  });

  it('does not let bulk price confirmation discard unsaved semantic choices', async () => {
    mount();
    const accepted = await screen.findByRole('button', { name: /Действие: Разваляне.*Очукване.*същото/ });
    const bulk = screen.getByRole('button', { name: /Confirm all.*suggested on this page/ });
    expect(bulk).not.toBeDisabled();
    fireEvent.click(accepted);
    expect(bulk).toBeDisabled();
    fireEvent.click(accepted);
    expect(bulk).not.toBeDisabled();
    expect(decideResult).not.toHaveBeenCalled();
  });

  it('labels previous human evidence without preselecting a fresh verdict', async () => {
    const row = rows[0]!;
    rows[0] = { ...row, factors: { ...row.factors, human_semantic_links: [link] } };
    mount();
    expect(await screen.findByText('Човек: потвърдено преди')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Действие: Разваляне.*Очукване.*същото/ }))
      .toHaveAttribute('aria-pressed', 'false');
  });

  it('prevents adopting a disputed job while persisting the rejected link', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /Действие: различно/ }));
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Nothing here fits' }));
    await waitFor(() => expect(decideResult).toHaveBeenCalledWith('review-one', expect.objectContaining({
      decision: 'rejected', semantic_choices: [{ link_id: link.id, verdict: 'different' }],
    })));
  });

  it('previews an alternative before overriding, preserving judgments on both quotations', async () => {
    const alternative = fixture.alternatives.find((candidate) => candidate.cost_item_id !== fixture.suggested_cost_item_id)!;
    vi.mocked(previewSemanticLinks).mockResolvedValue([link]);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: /Действие: различно/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Price against something else' }));
    const item = await screen.findByRole('button', { name: new RegExp(alternative.code) });
    fireEvent.click(item);
    await waitFor(() => expect(previewSemanticLinks).toHaveBeenCalledWith('review-one', alternative.cost_item_id));
    const preview = screen.getByRole('region', { name: 'Избрана оферта' });
    fireEvent.click(await within(preview).findByRole('button', { name: /Действие: Разваляне.*Очукване.*същото/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Потвърди тази оферта' }));
    await waitFor(() => expect(decideResult).toHaveBeenCalledWith('review-one', expect.objectContaining({
      decision: 'overridden', cost_item_id: alternative.cost_item_id,
      semantic_choices: [
        { link_id: link.id, verdict: 'different', cost_item_id: fixture.suggested_cost_item_id },
        { link_id: link.id, verdict: 'same', cost_item_id: alternative.cost_item_id },
      ],
    })));
  });
});

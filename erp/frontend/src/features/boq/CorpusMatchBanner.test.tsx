import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { CorpusMatchBanner } from './CorpusMatchBanner';
import { confirmBoqPositions, getActiveBoqMatchJob, listAllRuns, pollBoqMatchJob, fetchCorpusItemCount, type MatchRun } from '@/features/cost-match/api';

const mocks = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string, options?: Record<string, unknown>) =>
  String(options?.defaultValue ?? key).replace(/{{(\w+)}}/g, (_, name: string) => String(options?.[name] ?? '')) }) }));
vi.mock('@/stores/useToastStore', () => ({ useToastStore: (selector: (state: { addToast: typeof mocks.toast }) => unknown) => selector({ addToast: mocks.toast }) }));
vi.mock('@/shared/ui', async () => ({
  Button: (await import('@/shared/ui/Button')).Button,
  ConfirmDialog: ({ open, message, onConfirm }: { open: boolean; message: string; onConfirm: () => void }) =>
    open ? <div role="dialog"><p>{message}</p><button onClick={onConfirm}>Apply confirmation</button></div> : null,
}));
vi.mock('@/features/cost-match/api', () => ({
  confirmBoqPositions: vi.fn(), fetchCorpusItemCount: vi.fn(), getActiveBoqMatchJob: vi.fn(),
  listAllRuns: vi.fn(), pollBoqMatchJob: vi.fn(), startBoqMatchJob: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchCorpusItemCount).mockResolvedValue(10);
  vi.mocked(getActiveBoqMatchJob).mockResolvedValue({ job_id: 'job', boq_id: 'bill', status: 'running', started_at: '' });
  vi.mocked(listAllRuns).mockResolvedValue([]);
});

function mount(unconfirmedCount = 0) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}>
    <CorpusMatchBanner boqId="bill" projectId="project" unconfirmedCount={unconfirmedCount} />
  </QueryClientProvider></MemoryRouter>);
}

describe('CorpusMatchBanner', () => {
  it('explains incompatible skips and does not promise to learn matcher prices', async () => {
    vi.mocked(getActiveBoqMatchJob).mockResolvedValue(null);
    vi.mocked(confirmBoqPositions).mockResolvedValue({ boq_id: 'bill', confirmed: 2, skipped: 2, learned: 0 });
    mount(4);
    fireEvent.click(screen.getByTestId('corpus-confirm-all'));
    expect(screen.getByRole('dialog')).toHaveTextContent('Incompatible suggestions remain pending');
    expect(screen.getByRole('dialog')).toHaveTextContent('matcher-generated prices are not added');
    fireEvent.click(screen.getByRole('button', { name: 'Apply confirmation' }));
    await waitFor(() => expect(mocks.toast).toHaveBeenCalled());
    expect(mocks.toast.mock.calls[0]?.[0]).toMatchObject({
      title: '2 positions confirmed',
      message: expect.stringContaining('2 rows were not confirmed'),
    });
    expect(mocks.toast.mock.calls[0]?.[0].message).toContain('compatible or manual price');
    expect(mocks.toast.mock.calls[0]?.[0].message).not.toContain('nothing pending');
  });
  it('finishes a recovered job once and links to the actual bill/run, not a different latest run', async () => {
    vi.mocked(pollBoqMatchJob).mockResolvedValue({
      boq_id: 'bill', run_ids: ['run'], lines: 2, positions_priced: 1, positions_unpriced: 1,
      counts: { total: 2, exact: 1, high_confidence: 0, needs_review: 1, unmatched: 0, pending: 2,
        confirmed: 0, overridden: 0, rejected: 0, manual: 0, queue_length: 1 },
    });
    mount();
    const link = await screen.findByTestId('corpus-match-review');
    expect(link).toHaveAttribute('href', '/cost-match?boq_id=bill&project_id=project&run_id=run');
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    expect(pollBoqMatchJob).toHaveBeenCalledTimes(1);
    expect(mocks.toast).toHaveBeenCalledTimes(1);
    expect(mocks.toast.mock.calls[0]?.[0].message).toContain('need review or have no eligible price');
  });
  it('keeps a review link to historical runs after reload without needing a new completed job', async () => {
    vi.mocked(getActiveBoqMatchJob).mockResolvedValue(null);
    vi.mocked(listAllRuns).mockResolvedValue([{ id: 'historical', notes: '{"boq_id":"bill"}' } as MatchRun]);
    mount();
    const link = await screen.findByTestId('corpus-match-review');
    expect(link).toHaveAttribute('href', '/cost-match?boq_id=bill&project_id=project&run_id=historical');
    expect(pollBoqMatchJob).not.toHaveBeenCalled();
  });
  it('does not repeatedly re-attach to a failed recovered job', async () => {
    vi.mocked(pollBoqMatchJob).mockRejectedValue(new Error('failed'));
    mount();
    await waitFor(() => expect(mocks.toast).toHaveBeenCalled());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    expect(pollBoqMatchJob).toHaveBeenCalledTimes(1);
  });
});

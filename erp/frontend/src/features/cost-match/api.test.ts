import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiGet } from '@/shared/lib/api';
import { getActiveBoqMatchJob, getResult, listAllRuns, listReviewQueue, listRunResults, type MatchRun } from './api';
import fixture from './priceContract.fixture.json';

vi.mock('@/shared/lib/api', () => ({
  ApiError: class extends Error { constructor(public status: number) { super(String(status)); } },
  apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn(),
}));
beforeEach(() => vi.mocked(apiGet).mockReset());

describe('backend pricing contract', () => {
  it('treats only a missing job as idle, not a permission or server failure', async () => {
    vi.mocked(apiGet).mockRejectedValueOnce(new ApiError(404, 'Not Found', {}));
    expect(await getActiveBoqMatchJob('bill')).toBeNull();
    const error = new ApiError(500, 'Server Error', {});
    vi.mocked(apiGet).mockRejectedValueOnce(error);
    await expect(getActiveBoqMatchJob('bill')).rejects.toBe(error);
  });
  it('keeps decimal strings, quotation evidence and structured factors intact', async () => {
    vi.mocked(apiGet).mockResolvedValue(fixture);
    expect(await getResult(fixture.id, 'bg')).toEqual(fixture);
    expect(apiGet).toHaveBeenCalledWith(`/v1/cost-match/results/${fixture.id}?locale=bg`);
  });
  it('sends the requested offset for both the full record and queue', async () => {
    await listRunResults('run', { offset: 50, limit: 50, tier: 'needs_review' });
    await listReviewQueue('run', { offset: 100, limit: 50 });
    expect(apiGet).toHaveBeenNthCalledWith(1, '/v1/cost-match/runs/run/results?tier=needs_review&offset=50&limit=50');
    expect(apiGet).toHaveBeenNthCalledWith(2, '/v1/cost-match/runs/run/review-queue?offset=100&limit=50');
  });
  it('does not silently discard runs older than the first fifty', async () => {
    const first = Array.from({ length: 50 }, (_, i) => ({ id: `run-${i}` } as MatchRun));
    vi.mocked(apiGet).mockResolvedValueOnce(first).mockResolvedValueOnce([{ id: 'older-run' }]);
    const runs = await listAllRuns('project');
    expect(runs).toHaveLength(51);
    expect(runs.at(-1)?.id).toBe('older-run');
    expect(apiGet).toHaveBeenLastCalledWith('/v1/cost-match/runs/?project_id=project&offset=50&limit=50');
  });
});

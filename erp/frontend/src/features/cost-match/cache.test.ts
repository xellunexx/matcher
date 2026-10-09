import { describe, it, expect } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { refreshMatchPrices } from './cache';

describe('refreshMatchPrices', () => {
  it('invalidates prices, totals, resources, evidence and learned corpus counts together', () => {
    const client = new QueryClient();
    const keys = [['boq', 'bill'], ['boq-cost-breakdown', 'bill'], ['boq-resource-summary', 'bill'],
      ['cost-match', 'runs', 'project'], ['cost-match', 'results', 'run'], ['corpus-item-count']];
    keys.forEach((key) => client.setQueryData(key, { rate: 0 }));
    client.setQueryData(['boq', 'other'], { rate: 99 });
    refreshMatchPrices(client, 'bill');
    keys.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(true));
    expect(client.getQueryState(['boq', 'other'])?.isInvalidated).toBe(false);
    client.clear();
  });
});

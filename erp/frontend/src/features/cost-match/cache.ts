import type { QueryClient } from '@tanstack/react-query';

export function refreshMatchPrices(queryClient: QueryClient, boqId?: string | null): void {
  for (const key of ['boq', 'boq-cost-breakdown', 'boq-resource-summary']) {
    queryClient.invalidateQueries({ queryKey: boqId ? [key, boqId] : [key] });
  }
  queryClient.invalidateQueries({ queryKey: ['cost-match'] });
  queryClient.invalidateQueries({ queryKey: ['corpus-item-count'] });
}

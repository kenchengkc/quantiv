import { describe, expect, it } from 'vitest';
import { parseHomeSearchParams } from './homeSearchParams';

describe('calendar publication window', () => {
  it('opens the week containing the full 21-day earnings horizon', () => {
    expect(parseHomeSearchParams({ offset: '3', filter: 'all' }))
      .toEqual({ initialOffset: 3, initialFilter: 'all' });
  });
  it('keeps unsupported weeks outside the published window', () => {
    expect(parseHomeSearchParams({ offset: '4' }).initialOffset).toBe(0);
  });
});

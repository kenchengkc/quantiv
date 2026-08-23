import { describe, expect, it } from 'vitest';
import { parseMarketSymbol } from './marketSymbol';

describe('parseMarketSymbol', () => {
  it.each([
    ['aapl', 'AAPL'],
    [' BRK-B ', 'BRK-B'],
    ['BF.B', 'BF.B'],
    ['A1', 'A1'],
  ])('normalizes %s', (input, expected) => {
    expect(parseMarketSymbol(input)).toBe(expected);
  });

  it.each([
    '',
    '../secret',
    'AAPL?token=x',
    'AAPL\nforged-log',
    'AAPL/B',
    'TOO-LONG-SYMBOL',
    42,
    null,
  ])('rejects unsafe value %j', (input) => {
    expect(parseMarketSymbol(input)).toBeNull();
  });
});

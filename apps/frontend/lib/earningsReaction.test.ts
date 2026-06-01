import { describe, expect, it } from 'vitest';
import {
  earningsReactionCloseDate,
  isRealizationWindowComplete,
  nextTradingDayIso,
  resolveEarningsReactionDisplay,
  shouldPollLiveQuote,
} from './earningsReaction';

describe('earningsReaction', () => {
  it('AMC close date skips weekend after Friday report', () => {
    expect(earningsReactionCloseDate('2026-05-22', 'after_market_close')).toBe('2026-05-26');
    expect(nextTradingDayIso('2026-05-22')).toBe('2026-05-26');
  });

  it('BMO close date is the report day', () => {
    expect(earningsReactionCloseDate('2026-05-28', 'before_market_open')).toBe('2026-05-28');
  });

  it('shows REALIZED after BMO window with OHLCV', () => {
    const wedAfterClose = new Date('2026-05-28T21:00:00Z'); // 17:00 ET
    expect(
      isRealizationWindowComplete('2026-05-28', 'bmo', wedAfterClose),
    ).toBe(true);
    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-05-28',
      timing: 'bmo',
      realizedMovePct: 0.042,
      liveChangePct: 0.01,
      now: wedAfterClose,
    });
    expect(out).toEqual({ changePct: 0.042, tag: 'REALIZED' });
  });

  it('keeps BMO report day LIVE until the regular close', () => {
    const premarket = new Date('2026-05-28T12:00:00Z'); // 08:00 ET
    const oneMinuteBeforeClose = new Date('2026-05-28T19:59:00Z'); // 15:59 ET
    expect(isRealizationWindowComplete('2026-05-28', 'bmo', premarket)).toBe(false);
    expect(isRealizationWindowComplete('2026-05-28', 'bmo', oneMinuteBeforeClose)).toBe(false);

    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-05-28',
      timing: 'before_market_open',
      realizedMovePct: 0.04,
      liveChangePct: 0.018,
      now: oneMinuteBeforeClose,
    });
    expect(out).toEqual({ changePct: 0.018, tag: 'LIVE' });
  });

  it('shows LIVE on AMC report day before next session close', () => {
    const friAfterClose = new Date('2026-05-22T21:00:00Z');
    expect(
      isRealizationWindowComplete('2026-05-22', 'amc', friAfterClose),
    ).toBe(false);
    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-05-22',
      timing: 'amc',
      realizedMovePct: 0.05,
      liveChangePct: 0.02,
      now: friAfterClose,
    });
    expect(out).toEqual({ changePct: 0.02, tag: 'LIVE' });
  });

  it('shows REALIZED for AMC after next trading day close', () => {
    const tueAfterClose = new Date('2026-05-26T21:00:00Z');
    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-05-22',
      timing: 'amc',
      realizedMovePct: -0.03,
      liveChangePct: 0.01,
      now: tueAfterClose,
    });
    expect(out).toEqual({ changePct: -0.03, tag: 'REALIZED' });
  });

  it('suppresses LIVE for past events without OHLCV realized', () => {
    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-05-01',
      timing: 'bmo',
      realizedMovePct: null,
      liveChangePct: 0.015,
      now: new Date('2026-05-28T15:00:00Z'),
    });
    expect(out).toEqual({ changePct: null, tag: null });
  });

  it('shows LIVE on BMO report day after close when realized not yet recorded', () => {
    // OLLI/SAIC case: reported BMO today, it is past the close, but the nightly
    // build has not recorded realized_move_pct yet. The live close-to-close
    // equals the eventual realized move, so show LIVE rather than nothing.
    const reportDayAfterClose = new Date('2026-06-01T23:27:00Z'); // 19:27 ET
    expect(
      isRealizationWindowComplete('2026-06-01', 'bmo', reportDayAfterClose),
    ).toBe(true);
    const out = resolveEarningsReactionDisplay({
      earningsDate: '2026-06-01',
      timing: 'before_market_open',
      realizedMovePct: null,
      liveChangePct: -0.0208,
      now: reportDayAfterClose,
    });
    expect(out).toEqual({ changePct: -0.0208, tag: 'LIVE' });
    expect(
      shouldPollLiveQuote('2026-06-01', 'before_market_open', null, reportDayAfterClose),
    ).toBe(true);
  });

  it('shows LIVE on AMC realization day after close, but suppresses the day after', () => {
    // AMC reported 05-29 → realization completes 06-01 close.
    const realizationDayAfterClose = new Date('2026-06-01T21:00:00Z'); // 17:00 ET
    expect(
      resolveEarningsReactionDisplay({
        earningsDate: '2026-05-29',
        timing: 'amc',
        realizedMovePct: null,
        liveChangePct: 0.07,
        now: realizationDayAfterClose,
      }),
    ).toEqual({ changePct: 0.07, tag: 'LIVE' });

    // One day later, still no realized: the live anchor has rolled forward, so
    // it would no longer equal the earnings move — suppress instead of mislead.
    expect(
      resolveEarningsReactionDisplay({
        earningsDate: '2026-05-29',
        timing: 'amc',
        realizedMovePct: null,
        liveChangePct: 0.07,
        now: new Date('2026-06-02T17:00:00Z'),
      }),
    ).toEqual({ changePct: null, tag: null });
  });

  it('shouldPollLiveQuote is false when realized is shown', () => {
    expect(
      shouldPollLiveQuote('2026-05-01', 'bmo', -0.02, new Date('2026-05-28T15:00:00Z')),
    ).toBe(false);
  });
});

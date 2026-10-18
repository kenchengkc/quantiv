"""Carry a calendar's point-in-time historical context to matching symbol pages.

Legacy releases withheld upcoming headlines but retained calendar medians. Share
that exact context before enabling fallback rendering; never recompute history
from a symbol's later outcomes or copy an estimate to a different event.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from zoneinfo import ZoneInfo
from datetime import datetime
from pathlib import Path


def align_forecast_context(public: Path, *, today: date) -> int:
    events = {}
    for path in sorted((public / 'weeks').glob('????-??-??.json')):
        payload = json.loads(path.read_text())
        for event in payload.get('events', []):
            event_date = str(event.get('earnings_date') or '')[:10]
            reported = any(isinstance(event.get(key), (int, float))
                           and not isinstance(event.get(key), bool) and math.isfinite(event[key])
                           for key in ('eps_actual', 'revenue_actual', 'actual', 'realized_move_pct'))
            if event_date >= today.isoformat() and not reported:
                source_date = event.get('as_of_date') or (payload.get('metadata') or {}).get('as_of_date')
                events[(event.get('ticker'), event_date)] = (event, source_date)
    changed = 0
    for path in sorted((public / 'symbols').glob('*.json')):
        payload = json.loads(path.read_text())
        expected = payload.get('expected_move')
        if not isinstance(expected, dict):
            continue
        event_date = str(expected.get('earnings_date') or '')[:10]
        match = events.get((payload.get('symbol'), event_date))
        if match is None:
            continue
        event, source_date = match
        symbol_source = expected.get('as_of_date') or payload.get('as_of_date')
        if not source_date or source_date != symbol_source or source_date >= event_date:
            continue
        # Explicit null also prevents reconstructing a different median from
        # later compact history when this source has no historical estimate.
        value = event.get('hist_move_med_4q')
        if 'hist_move_med_4q' not in expected or expected['hist_move_med_4q'] != value:
            expected['hist_move_med_4q'] = value
            path.write_text(json.dumps(payload, indent=2) + '\n')
            changed += 1
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-dir', type=Path, default=Path('apps/frontend/public'))
    args = parser.parse_args()
    print(f'Aligned historical forecast context for {align_forecast_context(args.public_dir, today=datetime.now(ZoneInfo("America/New_York")).date())} symbols')

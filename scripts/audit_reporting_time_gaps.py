#!/usr/bin/env python3
"""Measure recovery of live unknown earnings sessions without publishing data."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import requests

from reconcile_earnings_calendar import (
    choose_canonical_event, et_today, fetch_finnhub_announcements, load_company_names,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-requests', type=int, default=3000)
    args = parser.parse_args()
    response = requests.get('https://usequantiv.com/calendar-reference.json', timeout=30)
    response.raise_for_status()
    reference = response.json()
    today = et_today()
    events = [r for r in reference['events'] if r['earnings_date'] >= today.isoformat() and r['timing'] == 'unknown']
    names = load_company_names()
    remaining = args.max_requests
    results = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index, event in enumerate(events):
        symbol = event['ticker']
        budget = min(12, remaining - (len(events) - index - 1))
        if budget < 1:
            break
        evidence, meta = fetch_finnhub_announcements(symbol, today=today,
            key=os.environ.get('FINNHUB_API_KEY'), company_name=names.get(symbol),
            max_requests=budget)
        remaining -= meta.get('requests', 0)
        anchor = {'provider': 'baseline', 'date': event['earnings_date'], 'timing': 'unknown'}
        decision = choose_canonical_event(current=anchor, baseline=anchor, structured_votes=[],
                                         announcements=evidence, as_of=today)
        results[symbol] = {'before': event, 'decision': decision, 'evidence': evidence, 'retrieval': meta}
        args.output.write_text(json.dumps({'generated_at': datetime.now(timezone.utc).isoformat(),
            'calendar_release_id': reference['release_id'], 'baseline_unknown': len(events),
            'requests': args.max_requests - remaining, 'results': results}, indent=2) + '\n')
        if (index+1) % 25 == 0:
            counts = Counter(x['decision']['timing'] for x in results.values())
            print(f'Checked {index+1}/{len(events)}; sessions={dict(counts)}; requests={args.max_requests-remaining}', flush=True)
        time.sleep(1.05)
    print(f'Audit complete: {len(results)} events, {args.max_requests-remaining} requests', flush=True)


if __name__ == '__main__':
    main()

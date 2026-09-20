#!/usr/bin/env python3
"""
Build frontend data from local parquet files.

Generates:
  public/weekly.json         — current week's earnings + expected moves
  public/screener.json       — merged, deduped earnings rows for /screener (single fetch)
  public/symbols/{SYM}.json  — per-symbol detail with implied moves, Greeks, term structure

Uses the most recent parquet snapshot as the as-of date. Safe to re-run.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "apps" / "ml"))
sys.path.append(str(Path(__file__).resolve().parent.parent / "scripts"))
from build_earnings_events import build_earnings_events_table, create_duckdb_views
from event_forecast_ledger import record_publications
from frontend_data.display_payloads import (
    build_display_forecast_status,
    enrich_upcoming_events,
    ensure_symbol_display_detail,
    validate_upcoming_display_forecasts,
)
from frontend_data.forecast_artifacts import (
    build_dashboard_evidence as build_dashboard_evidence,
    load_event_forecast_archive,
    load_ml_forecasts,
    load_provider_enrichments,
    provider_event_fields as provider_event_fields,
    publish_forecast_evidence,
)
from frontend_data.payloads import (
    align_symbol_detail_to_published,
    attach_frozen_event_forecasts,
    apply_published_canonical,
    apply_published_symbol_dates,
    build_screener_payload,
    build_symbol_detail,
    build_week_events,
    collapse_duplicate_earnings,
    enrich_reported_event_forecasts,
    load_published_calendar_events,
    preserve_reported_events,
    published_symbol_dates,
    week_em_averages,
)
from frontend_data.realized_moves import (
    enrich_hist_move_avg_from_twelvedata,
    enrich_realized_moves_from_ohlcv,
    enrich_realized_moves_from_twelvedata,
    monday_of_week,
    validate_twelvedata_against_ohlcv,
)
from frontend_data.shared import (
    DATA_DIR,
    EARNINGS_CSV,
    PUBLIC_DIR,
    WEEK_OFFSETS,
    write_to_public,
)


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip symbol detail files that were written AFTER weekly.json "
            "in this run. Use when recovering from a crash mid-loop so you "
            "don't re-do hundreds of completed tickers."
        ),
    )
    ap.add_argument(
        "--skip-weeks",
        action="store_true",
        help="Skip the weeks rebuild entirely — jump straight into symbol details. "
             "Implied by --resume.",
    )
    ap.add_argument(
        "--twelvedata-dry-run",
        action="store_true",
        help=(
            "Print TwelveData realized-move fallback candidates, estimated credits, "
            "remaining daily quota, and skipped events without calling TwelveData."
        ),
    )
    args = ap.parse_args()

    (PUBLIC_DIR / "symbols").mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "weeks").mkdir(parents=True, exist_ok=True)
    publish_forecast_evidence()

    conn = duckdb.connect()
    # Cap DuckDB memory so long-running symbol-detail loops don't OOM-segfault.
    # Spills to disk if the working set exceeds this.
    conn.execute("PRAGMA memory_limit='4GB'")
    conn.execute("PRAGMA threads=4")
    build_earnings_events_table(conn, EARNINGS_CSV)
    create_duckdb_views(conn, DATA_DIR)
    ml_lookup = load_ml_forecasts()
    event_forecast_archive = load_event_forecast_archive()
    provider_lookup = load_provider_enrichments()
    published_forecast_ids: set[str] = set()

    as_of_row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
    as_of_date = as_of_row[0]
    if as_of_date is None:
        print("❌ No options data available")
        sys.exit(1)

    # Staleness guardrail. CI's daily-refresh pulls fresh parquet from R2
    # before building, so as_of_date should be within a few days of today.
    # Running this script with weeks-old local parquet produces empty
    # upcoming-week JSONs and silently regresses production data. Bail
    # loudly instead. Set ALLOW_STALE_OPTIONS=1 to override (e.g. for
    # offline development or testing historical snapshots).
    age_days = (date.today() - as_of_date).days
    stale_threshold = int(os.getenv("STALE_OPTIONS_MAX_DAYS", "7"))
    if age_days > stale_threshold and not os.getenv("ALLOW_STALE_OPTIONS"):
        print(
            f"❌ Options chain is {age_days} days stale "
            f"(as_of={as_of_date}, today={date.today()}, "
            f"threshold={stale_threshold}d).\n"
            "   Run `bash scripts/r2_pull.sh` to refresh local parquet, "
            "or set ALLOW_STALE_OPTIONS=1 to override.\n"
            "   Building with stale data produces empty upcoming-week "
            "JSONs and regresses production. Aborting.",
            file=sys.stderr,
        )
        sys.exit(2)
    if age_days > 1:
        print(f"⚠ Options chain is {age_days} days old (as_of={as_of_date})")

    today = date.today()
    this_monday = monday_of_week(today) if today.weekday() < 5 else today + timedelta(days=(7 - today.weekday()))

    # Build each week (last, this, next, +2) and collect unique tickers for detail pages.
    # On --resume: reuse the existing per-week JSONs on disk — don't rebuild them.
    skip_weeks = args.skip_weeks or args.resume
    week_payloads = {}
    tickers_needing_detail: dict[str, date] = {}

    # Collapse revised/duplicate forward earnings dates to one per ticker across
    # the whole window so weekly/weeks/screener stay consistent (a revised
    # estimate like PLUS 5/20→5/27→5/28 otherwise shows 2-3 times). Prefer the
    # row with reported actuals (the confirmed date), else the latest revision.
    span_start = this_monday + timedelta(days=7 * min(WEEK_OFFSETS))
    span_end = this_monday + timedelta(days=7 * max(WEEK_OFFSETS) + 4)
    canonical_keys, dropped_dupes = collapse_duplicate_earnings(conn, span_start, span_end)
    if dropped_dupes:
        print(
            f"  Earnings dedup: collapsed {len(dropped_dupes)} duplicate date(s) "
            "to one per ticker (actuals first, else latest):"
        )
        for tk, dropped_iso, kept in sorted(dropped_dupes):
            print(f"    {tk}: dropped {dropped_iso} → kept {kept}")

    # Event identities the currently published calendar already displays. These
    # rows render on the homepage whether or not research produced a row for
    # them, so they must not be dropped by the ML-coverage gate — see
    # payloads.ml_gate_drops_event. Canonical collapse must also keep these
    # dates: a later DuckDB revision (AIR 9/21→9/22) otherwise prices a day
    # the overlay will not attach to.
    published_events = load_published_calendar_events(PUBLIC_DIR)
    published_keys = {(ticker, earn_dt.isoformat()) for ticker, earn_dt, _ in published_events}
    published_timings = {
        (ticker, earn_dt.isoformat()): timing for ticker, earn_dt, timing in published_events
    }
    published_for_symbols = published_symbol_dates(published_events, today)
    canonical_keys, published_swaps = apply_published_canonical(canonical_keys, published_keys)
    print(f"  Published calendar reference: {len(published_keys)} event identities")
    if published_swaps:
        print(
            f"  Published calendar dates replaced {len(published_swaps)} "
            "DuckDB canonical date(s):"
        )
        for tk, dropped_iso, kept in published_swaps:
            print(f"    {tk}: dropped {dropped_iso} → kept {kept} (calendar)")

    if skip_weeks:
        print("⏭️  --resume/--skip-weeks: reading existing weeks/*.json from disk", flush=True)
        for offset in WEEK_OFFSETS:
            wk_start = this_monday + timedelta(days=7 * offset)
            wk_path = PUBLIC_DIR / "weeks" / f"{wk_start.isoformat()}.json"
            if not wk_path.exists():
                print(f"  ❌ missing {wk_path.name} — run without --resume to rebuild it")
                sys.exit(1)
            payload = json.loads(wk_path.read_text())
            events = payload.get("events", [])
            enrich_reported_event_forecasts(
                conn,
                events,
                today=today,
                archive=event_forecast_archive,
            )
            enrich_upcoming_events(conn, events, as_of_date=as_of_date, today=today)
            week_payloads[wk_start] = payload
            published_forecast_ids.update(
                str(event.get("forecast_id"))
                for event in events
                if event.get("forecast_id")
            )
            print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events (cached)")
            for ev in events:
                tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    for offset in ([] if skip_weeks else WEEK_OFFSETS):
        wk_start = this_monday + timedelta(days=7 * offset)
        wk_end = wk_start + timedelta(days=4)
        # daily_score.py only scores upcoming earnings within a ~21-day window,
        # so require_ml would leave last/+2 weeks empty. Fall back to the
        # options_math baseline for those; enforce ML on current + next week.
        require_ml = offset in (0, 1)
        events = build_week_events(conn, as_of_date, wk_start, wk_end, ml_lookup, provider_lookup,
                                   require_ml=require_ml, canonical=canonical_keys,
                                   published=published_timings)

        # Reported-event preservation: an expected move is only observable
        # before the print, so once an earnings date passes compute_em_math
        # returns None for it forever and the rebuild drops the row — the
        # calendar keeps the event (calendar-reference is authoritative for
        # membership) but renders it without a forecast.
        #
        # This has to run while the week is still current. Reporters erode from
        # the bundle the day after they report, so a guard that waited for the
        # whole week to end only ever read back a file that had already lost
        # its Mon-Thu rows: the Sep 7 week went 13 events (Tue) → 9 (Wed) →
        # 5 (Thu), and preservation then faithfully retained the surviving 5.
        if wk_start <= today:
            wk_path = PUBLIC_DIR / "weeks" / f"{wk_start.isoformat()}.json"
            if wk_path.exists():
                try:
                    prior = json.loads(wk_path.read_text())
                    merged = preserve_reported_events(
                        events, prior.get("events", []), today, canonical_keys
                    )
                    if len(merged) > len(events):
                        print(
                            f"    preserving {len(merged) - len(events)} reported events "
                            "from prior bundle (pre-event options no longer observable)"
                        )
                    events = merged
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"    ⚠ could not read prior bundle: {exc}")

        reported_forecasts = enrich_reported_event_forecasts(
            conn,
            events,
            today=today,
            archive=event_forecast_archive,
        )
        if reported_forecasts:
            print(
                f"    frozen reported forecasts refreshed: {reported_forecasts}",
                flush=True,
            )

        ohlcv_realized = enrich_realized_moves_from_ohlcv(conn, events)
        twelve_realized = enrich_realized_moves_from_twelvedata(
            events,
            dry_run=args.twelvedata_dry_run,
            label=f"week {wk_start.isoformat()}",
        )
        if ohlcv_realized or twelve_realized:
            print(
                f"    realized moves updated: {ohlcv_realized} from OHLCV"
                + (f", {twelve_realized} from TwelveData" if twelve_realized else ""),
                flush=True,
            )

        # Presentation fallbacks resolve only after the strict research row is
        # built. This keeps ML/decision eligibility untouched while guaranteeing
        # a useful headline for every upcoming published event.
        enrich_upcoming_events(conn, events, as_of_date=as_of_date, today=today)

        print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events")
        avg_em_straddle_pct, avg_em_iv_pct = week_em_averages(events)
        payload = {
            "metadata": {
                "version": "v4_multi_week",
                "generated_at": datetime.now().isoformat(),
                "as_of_date": as_of_date.isoformat(),
                "method": "ATM straddle + IV baseline",
                "offset": offset,
            },
            "window": {"start": wk_start.isoformat(), "end": wk_end.isoformat()},
            "events": events,
            "summary": {
                "total_events": len(events),
                "avg_em_straddle_pct": avg_em_straddle_pct,
                "avg_em_iv_pct": avg_em_iv_pct,
            },
        }
        week_payloads[wk_start] = payload
        published_forecast_ids.update(
            str(event.get("forecast_id"))
            for event in events
            if event.get("forecast_id")
        )
        write_to_public(
            f"weeks/{wk_start.isoformat()}.json",
            json.dumps(payload, indent=2, default=str),
        )
        for ev in events:
            tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    if not skip_weeks:
        # Spend TwelveData on every realized-move gap first. Historical averages
        # only use whatever Basic-tier quota remains after all weekly rows are built.
        twelve_hist_total = 0
        for offset in WEEK_OFFSETS:
            wk_start = this_monday + timedelta(days=7 * offset)
            payload = week_payloads.get(wk_start)
            if not payload:
                continue
            twelve_hist = enrich_hist_move_avg_from_twelvedata(
                conn,
                payload.get("events", []),
                dry_run=args.twelvedata_dry_run,
                label=f"week {wk_start.isoformat()}",
            )
            if twelve_hist:
                twelve_hist_total += twelve_hist
                write_to_public(
                    f"weeks/{wk_start.isoformat()}.json",
                    json.dumps(payload, indent=2, default=str),
                )
        if twelve_hist_total:
            print(
                f"    hist avg updated: {twelve_hist_total} from TwelveData remaining quota",
                flush=True,
            )
        validation_events = [
            ev
            for offset in WEEK_OFFSETS
            for ev in week_payloads.get(
                this_monday + timedelta(days=7 * offset),
                {},
            ).get("events", [])
        ]
        validate_twelvedata_against_ohlcv(
            conn,
            validation_events,
            dry_run=args.twelvedata_dry_run,
            label="weekly calendar",
        )

    # Never-dash is a build contract, not a React fallback. Validate before
    # manifest/screener publication and retain a compact runtime status artifact
    # for the control-plane projection.
    validate_upcoming_display_forecasts(
        published_events,
        week_payloads,
        today=today,
    )
    display_status = build_display_forecast_status(
        published_events,
        week_payloads,
        today=today,
        generated_at=datetime.now().isoformat(),
    )
    mix = display_status["method_mix"]
    print(
        "  forecast methods: "
        + " ".join(f"{method}={mix.get(method, 0)}" for method in sorted(mix)),
        flush=True,
    )
    validation_dir = DATA_DIR / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / "display_forecast_status.json").write_text(
        json.dumps(display_status, indent=2), encoding="utf-8"
    )

    # Primary weekly.json points at the current week (back-compat for any old consumers).
    if not skip_weeks:
        write_to_public("weekly.json", json.dumps(week_payloads[this_monday], indent=2, default=str))

    # Manifest of available weeks (used by the UI to render nav state).
    if not skip_weeks:
        manifest = {
            "as_of_date": as_of_date.isoformat(),
            "current_week": this_monday.isoformat(),
            "weeks": [
                {
                    "start": d.isoformat(),
                    "end": (d + timedelta(days=4)).isoformat(),
                    "offset": offset,
                    "count": len(week_payloads[d]["events"]),
                }
                for offset, d in ((off, this_monday + timedelta(days=7 * off)) for off in WEEK_OFFSETS)
            ],
        }
        write_to_public("weeks/manifest.json", json.dumps(manifest, indent=2))

        screener = build_screener_payload(as_of_date, this_monday, week_payloads)
        write_to_public("screener.json", json.dumps(screener, indent=2, default=str))
        print(f"📊 screener.json → {screener['metadata']['event_count']} events", flush=True)

    # Generate per-symbol detail: all tickers seen in any week + a curated popular list.
    popular = [
        "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "META", "GOOGL", "JPM",
        "BAC", "GS", "NFLX", "JNJ", "UNH", "PG", "V", "MA", "IBM", "INTC",
        "T", "BA", "GE", "RTX", "AXP", "HON", "LMT", "TMO", "MCO",
    ]
    for ticker in popular:
        if ticker not in tickers_needing_detail:
            row = conn.execute(
                """
                SELECT earnings_dt FROM earnings_events
                WHERE ticker = ? AND earnings_dt >= ?
                ORDER BY earnings_dt LIMIT 1
                """,
                [ticker, as_of_date],
            ).fetchone()
            tickers_needing_detail[ticker] = row[0] if row else None

    apply_published_symbol_dates(tickers_needing_detail, published_for_symbols)

    display_events = {
        (str(event.get("ticker") or "").upper(), str(event.get("earnings_date") or "")[:10]): event
        for payload in week_payloads.values()
        for event in payload.get("events", [])
        if event.get("ticker") and event.get("earnings_date")
    }

    generated = 0
    skipped_resume = 0
    total_details = len(tickers_needing_detail)
    if args.resume:
        print(
            "⏭️  --resume: skipping any ticker whose symbols/{TICKER}.json already exists. "
            "Files from previous runs stay — run without --resume for a full refresh.",
            flush=True,
        )

    print(f"📝 generating {total_details} symbol detail files", flush=True)
    # Print every ticker so a segfault leaves the exact culprit on the last
    # visible line. (Cheap — ~1000 lines over a 10-min run.)
    for i, (ticker, earn_dt) in enumerate(tickers_needing_detail.items(), 1):
        if args.resume and (PUBLIC_DIR / "symbols" / f"{ticker}.json").exists():
            skipped_resume += 1
            continue
        print(f"  [{i}/{total_details}] {ticker}", flush=True)
        # Recycle the DuckDB connection every 200 tickers to shed any
        # accumulated query-plan memory that could push us toward a segfault.
        if i > 1 and i % 200 == 0:
            conn.close()
            conn = duckdb.connect()
            conn.execute("PRAGMA memory_limit='4GB'")
            conn.execute("PRAGMA threads=4")
            build_earnings_events_table(conn, EARNINGS_CSV)
            create_duckdb_views(conn, DATA_DIR)
        try:
            detail = build_symbol_detail(conn, ticker, as_of_date, earn_dt, ml_lookup, provider_lookup)
            display_event = (
                display_events.get((ticker.upper(), earn_dt.isoformat()))
                if earn_dt is not None
                else None
            )
            detail = ensure_symbol_display_detail(
                conn,
                detail,
                ticker=ticker,
                as_of_date=as_of_date,
                earnings_date=earn_dt,
                display_event=display_event,
                provider_fields=provider_event_fields((provider_lookup or {}).get(ticker.upper())),
            )
            if not detail:
                continue
            # Attach timing (BMO/AMC/unknown) from the earnings row, surfaced on the detail page.
            if earn_dt:
                row = conn.execute(
                    "SELECT timing FROM earnings_events WHERE ticker = ? AND earnings_dt = ? LIMIT 1",
                    [ticker, earn_dt],
                ).fetchone()
                timing = (row[0] if row else None) or "unknown"
                detail["next_earnings_timing"] = timing
                if detail.get("expected_move"):
                    detail["expected_move"]["timing"] = timing
                    # em_method is already set inside build_symbol_detail based
                    # on whether an ML forecast was attached; don't overwrite.
            published = published_for_symbols.get(ticker)
            if published:
                align_symbol_detail_to_published(detail, published[0], published[1])
            detail = attach_frozen_event_forecasts(
                detail,
                ticker,
                event_forecast_archive,
                current_event_date=published[0] if published else earn_dt,
                today=today,
            )
            forecast_nodes = [detail.get("expected_move") or {}]
            forecast_nodes.extend(detail.get("earnings_history") or [])
            published_forecast_ids.update(
                str(node.get("forecast_id"))
                for node in forecast_nodes
                if isinstance(node, dict) and node.get("forecast_id")
            )
            write_to_public(
                f"symbols/{ticker}.json",
                json.dumps(detail, indent=2, default=str),
            )
            generated += 1
        except Exception as e:
            print(f"  ⚠️  {ticker} detail: {e}")

    publication_path = record_publications(
        published_forecast_ids,
        DATA_DIR / "forecasts",
        published_at=datetime.now(timezone.utc),
    )
    if publication_path is not None:
        print(
            f"🧾 recorded publication receipts for "
            f"{len(published_forecast_ids)} frozen forecast(s) → "
            f"{publication_path.name}",
            flush=True,
        )

    print(
        f"✅ {len(week_payloads)} weeks written, {generated} symbol files"
        + (f", {skipped_resume} skipped (resume)" if skipped_resume else "")
    )
    conn.close()


if __name__ == "__main__":
    main()

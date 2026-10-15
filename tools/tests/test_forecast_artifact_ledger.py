from __future__ import annotations

from pathlib import Path
from datetime import date
import json

import pandas as pd
import pytest

import frontend_data.forecast_artifacts as artifacts


def test_load_ml_forecasts_prefers_latest_eligible_ledger_row(
    tmp_path: Path,
    monkeypatch,
):
    forecast_dir = tmp_path / "forecasts"
    forecast_dir.mkdir()
    ledger_path = forecast_dir / "event_prediction_ledger.parquet"

    pd.DataFrame(
        [
            {
                "act_symbol": "M",
                "earnings_date": "2026-09-10",
                "snapshot_date": "2026-09-08",
                "feature_snapshot_at": "2026-09-08T20:00:00+00:00",
                "scored_at": "2026-09-09T15:35:05+00:00",
                "model_horizon": 2,
                "em_ml_pct": 0.061359,
                "freeze_eligible": True,
            },
            {
                "act_symbol": "M",
                "earnings_date": "2026-09-10",
                "snapshot_date": "2026-09-09",
                "feature_snapshot_at": "2026-09-09T20:00:00+00:00",
                "scored_at": "2026-09-10T15:42:00+00:00",
                "model_horizon": 1,
                "em_ml_pct": 0.055115,
                "freeze_eligible": False,
                "freeze_ineligible_reason": "prediction_created_after_deadline",
            },
        ]
    ).to_parquet(ledger_path, index=False)

    monkeypatch.setattr(artifacts, "FORECASTS_DIR", forecast_dir)
    monkeypatch.setattr(artifacts, "EVENT_PREDICTION_LEDGER_PATH", ledger_path)

    loaded = artifacts.load_ml_forecasts()

    row = loaded[("M", "2026-09-10")]
    assert row["em_ml_pct"] == 0.061359
    assert row["freeze_eligible"] is True


def test_authenticated_selection_does_not_recover_older_held_ledger_rows(tmp_path, monkeypatch):
    ledger = tmp_path / 'event_prediction_ledger.parquet'
    pd.DataFrame([
        {'act_symbol': symbol, 'earnings_date': '2026-10-20',
         'snapshot_date': '2026-10-12', 'feature_snapshot_at': '2026-10-12T20:00:00Z',
         'scored_at': '2026-10-13T10:00:00Z', 'freeze_eligible': True,
         'forecast_id': identity, 'em_ml_pct': .08}
        for symbol, identity in [('GOOD', 'approved'), ('HELD', 'older-held'), ('OTHER', 'unlisted')]
    ]).to_parquet(ledger, index=False)
    monkeypatch.setattr(artifacts, 'EVENT_PREDICTION_LEDGER_PATH', ledger)
    loaded = artifacts.load_ml_forecasts(eligible_forecast_ids={'approved'})
    assert set(loaded) == {('GOOD', '2026-10-20')}
    assert artifacts.load_ml_forecasts(eligible_forecast_ids=set()) == {}


def test_exact_subset_is_authoritative_when_legacy_directories_are_absent(tmp_path, monkeypatch):
    subset = tmp_path / 'eligible.parquet'
    pd.DataFrame([{'act_symbol': 'good', 'earnings_date': '2026-10-20',
                   'snapshot_date': '2026-10-12', 'model_horizon': 7,
                   'forecast_id': 'approved', 'freeze_eligible': True, 'em_ml_pct': .08}])\
        .to_parquet(subset, index=False)
    monkeypatch.setattr(artifacts, 'FORECASTS_DIR', tmp_path / 'missing-legacy')
    monkeypatch.setattr(artifacts, 'EVENT_PREDICTION_LEDGER_PATH', tmp_path / 'missing-ledger')
    loaded = artifacts.load_ml_forecasts(forecast_path=subset, eligible_forecast_ids={'approved'},
                                         eligible_event_keys={'GOOD|2026-10-20'})
    assert set(loaded) == {('GOOD', '2026-10-20')}
    assert artifacts.load_ml_forecasts(forecast_path=subset, eligible_forecast_ids={'approved'},
                                       eligible_event_keys=set()) == {}


def test_partial_hold_cleans_retained_surfaces_and_preserves_reported_history(tmp_path):
    (tmp_path / 'symbols').mkdir()
    (tmp_path / 'weeks').mkdir()
    past = {'date': '2026-10-01', 'em_ml_pct': .04, 'forecast_id': 'reported'}
    rows = []
    for symbol, identity in [('GOOD', 'approved'), ('HELD', 'older-held'), ('OTHER', 'unlisted')]:
        node = {'earnings_date': '2026-10-20', 'em_ml_pct': .09, 'p90': .2,
                'forecast_id': identity, 'display_forecast_method': 'ml',
                'display_forecast_pct': .09, 'iv_pct': .07}
        (tmp_path / 'symbols' / f'{symbol}.json').write_text(json.dumps({
            'symbol': symbol, 'as_of_date': '2026-10-12', 'expected_move': node,
            'earnings_history': [past]}))
        rows.append({'ticker': symbol, 'as_of_date': '2026-10-12', **node})
    for relative in ['weekly.json', 'screener.json', 'weeks/2026-10-19.json']:
        (tmp_path / relative).write_text(json.dumps({'events': rows}))
    artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 13),
        eligible_forecast_ids={'approved'}, held_event_reasons={'HELD|2026-10-20': 'unsupported_cohort'})
    for relative in ['weekly.json', 'screener.json', 'weeks/2026-10-19.json']:
        events = json.loads((tmp_path / relative).read_text())['events']
        assert events[0]['em_ml_pct'] == .09
        assert events[0]['ml_publication_status'] == 'eligible'
        for row in events[1:]:
            assert 'em_ml_pct' not in row and 'forecast_id' not in row and 'p90' not in row
            assert row['ml_publication_status'] == 'held'
            assert row['display_forecast_method'] == 'ml_unavailable'
            assert row['display_forecast_pct'] is None
        assert events[1]['ml_publication_reason'] == 'unsupported_cohort'
    assert json.loads((tmp_path / 'symbols/HELD.json').read_text())['earnings_history'] == [past]


def test_subset_evidence_cannot_publish_a_receipt_for_the_full_candidate(tmp_path, monkeypatch):
    from ml.evidence_receipt import build_evidence_receipt
    raw = tmp_path / 'candidate.parquet'
    subset = tmp_path / 'eligible.parquet'
    raw.write_bytes(b'full candidate rows')
    subset.write_bytes(b'only eligible rows')
    receipt = build_evidence_receipt(
        {'status': 'passed', 'stages': {'forecasts': {'rows': 2, 'events': 2}}, 'issues': []},
        scope='forecasts', repo_root=tmp_path, data_dir=tmp_path,
        training_dir=tmp_path / 'training', models_dir=tmp_path / 'models', forecast_path=raw,
        horizons=[7])
    receipt_path = tmp_path / 'receipt.json'
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(artifacts, 'FORECAST_RECEIPT_PATH', receipt_path)
    monkeypatch.setattr(artifacts, 'PUBLIC_DIR', tmp_path / 'public')
    with pytest.raises(ValueError, match='eligible forecast'):
        artifacts.publish_forecast_evidence(expected_forecast_path=subset)


def test_retained_calendar_cannot_recover_old_id_from_an_eligible_symbol(tmp_path):
    (tmp_path / 'symbols').mkdir()
    node = {'earnings_date': '2026-10-20', 'em_ml_pct': .09,
            'forecast_id': 'approved', 'display_forecast_method': 'ml',
            'display_forecast_pct': .09, 'iv_pct': .07}
    (tmp_path / 'symbols/GOOD.json').write_text(json.dumps({
        'symbol': 'GOOD', 'as_of_date': '2026-10-12', 'expected_move': node,
        'earnings_history': []}))
    (tmp_path / 'weekly.json').write_text(json.dumps({'events': [{
        **node, 'ticker': 'GOOD', 'as_of_date': '2026-10-12', 'forecast_id': 'old-id'}]}))
    artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 13), eligible_forecast_ids={'approved'})
    row = json.loads((tmp_path / 'weekly.json').read_text())['events'][0]
    assert row.get('forecast_id') is None
    assert row['display_forecast_method'] == 'ml_unavailable'
    assert row['display_forecast_pct'] is None


@pytest.mark.parametrize('timing', ['bmo', 'amc'])
def test_same_day_unreported_events_require_exact_eligibility(tmp_path, timing):
    (tmp_path / 'symbols').mkdir()
    (tmp_path / 'weeks').mkdir()
    rows = []
    for symbol, identity in [('HELD', 'old-held'), ('GOOD', 'approved'), ('REPORTED', 'reported')]:
        node = {'earnings_date': '2026-10-13', 'timing': timing,
                'em_ml_pct': .09, 'p90': .2, 'forecast_id': identity,
                'display_forecast_method': 'ml', 'display_forecast_pct': .09,
                'iv_pct': .07, 'forecast_frozen': True}
        history = [{'date': '2026-10-13', 'timing': timing, 'em_ml_pct': .09,
                    'forecast_id': identity, 'eps_actual': 0 if symbol == 'REPORTED' else None}]
        payload = {'symbol': symbol, 'as_of_date': '2026-10-12',
                   'expected_move': node, 'earnings_history': history}
        (tmp_path / 'symbols' / f'{symbol}.json').write_text(json.dumps(payload))
        rows.append({'ticker': symbol, 'as_of_date': '2026-10-12', **node,
                     'eps_actual': 0 if symbol == 'REPORTED' else None})
    original_reported = (tmp_path / 'symbols/REPORTED.json').read_text()
    for relative in ['weekly.json', 'screener.json', 'weeks/2026-10-12.json']:
        (tmp_path / relative).write_text(json.dumps({'events': rows}))
    artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 13),
                                  eligible_forecast_ids={'approved'})
    for relative in ['weekly.json', 'screener.json', 'weeks/2026-10-12.json']:
        events = json.loads((tmp_path / relative).read_text())['events']
        assert events[0]['ml_publication_status'] == 'held'
        assert events[0].get('em_ml_pct') is None and events[0].get('forecast_id') is None
        assert events[0]['display_forecast_method'] == 'ml_unavailable'
        assert events[1]['ml_publication_status'] == 'eligible' and events[1]['em_ml_pct'] == .09
        assert events[2] == rows[2]
    held = json.loads((tmp_path / 'symbols/HELD.json').read_text())
    assert held['expected_move']['ml_publication_status'] == 'held'
    assert held['earnings_history'][0].get('em_ml_pct') is None
    assert (tmp_path / 'symbols/REPORTED.json').read_text() == original_reported


def test_same_day_reported_symbol_history_preserves_calendar_without_actual_fields(tmp_path):
    (tmp_path / 'symbols').mkdir()
    expected = {'earnings_date': '2026-10-13', 'forecast_id': 'reported', 'em_ml_pct': .04}
    (tmp_path / 'symbols/REPORTED.json').write_text(json.dumps({
        'symbol': 'REPORTED', 'as_of_date': '2026-10-12', 'expected_move': expected,
        'earnings_history': [{'date': '2026-10-13', 'revenue_actual': 0}]}))
    row = {'ticker': 'REPORTED', **expected}
    (tmp_path / 'weekly.json').write_text(json.dumps({'events': [row]}))
    artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 13), eligible_forecast_ids=set())
    assert json.loads((tmp_path / 'weekly.json').read_text())['events'] == [row]


def test_archive_retains_same_day_rows_only_when_reported_or_currently_eligible():
    archive = {
        ('PAST', '2026-10-12'): {'forecast_id': 'past'},
        ('HELD', '2026-10-13'): {'forecast_id': 'old-held'},
        ('GOOD', '2026-10-13'): {'forecast_id': 'approved'},
        ('REPORTED', '2026-10-13'): {'forecast_id': 'reported'},
    }
    selected = artifacts.filter_event_forecast_archive_for_publication(
        archive, today=date(2026, 10, 13), eligible_forecast_ids={'approved'},
        eligible_event_keys={'GOOD|2026-10-13'}, reported_event_keys={'REPORTED|2026-10-13'})
    assert set(selected) == {('PAST', '2026-10-12'), ('GOOD', '2026-10-13'), ('REPORTED', '2026-10-13')}


def test_ml_fields_preserve_exact_model_publication_identity():
    fields = artifacts.ml_fields({'forecast_id': 'approved', 'model_bundle_id': 'active-bundle',
                                 'scored_at': '2026-10-13T10:00:00Z',
                                 'snapshot_date': date(2026, 10, 12), 'em_ml_pct': .08})
    assert fields['forecast_model_bundle_id'] == 'active-bundle'
    assert fields['forecast_scored_at'] == '2026-10-13T10:00:00Z'
    assert fields['ml_snapshot_date'] == '2026-10-12'


def test_same_day_frozen_symbol_keeps_the_model_binding_fields():
    from frontend_data.payloads import attach_frozen_event_forecasts
    detail = {'earnings_history': [{'date': '2026-10-13', 'timing': 'amc'}]}
    archive = {('GOOD', '2026-10-13'): {
        'forecast_id': 'approved', 'model_bundle_id': 'active-bundle', 'model_horizon': 1,
        'snapshot_date': date(2026, 10, 12), 'scored_at': '2026-10-13T10:00:00Z', 'em_ml_pct': .08}}
    result = attach_frozen_event_forecasts(detail, 'GOOD', archive,
        current_event_date=date(2026, 10, 13), today=date(2026, 10, 13))
    assert result['expected_move']['forecast_model_bundle_id'] == 'active-bundle'
    assert result['expected_move']['forecast_scored_at'] == '2026-10-13T10:00:00Z'
    assert result['expected_move']['ml_snapshot_date'] == '2026-10-12'

from scripts.event_forecast_eligibility import _current_source_coverage
from scripts.tests.test_corporate_action_quarantine import _quarantined_receipt


def test_monitor_source_scope_uses_genuine_receipt_reconstruction(tmp_path, monkeypatch):
    _quarantined_receipt(tmp_path, monkeypatch)
    coverage = _current_source_coverage(tmp_path / "models/bundles/unused")
    assert "AAPL" not in coverage and "APLD" not in coverage
    assert coverage["LEVI"] == ("2019-01-01", "2026-10-11")

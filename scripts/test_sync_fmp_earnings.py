import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from sync_fmp_earnings import FMPQuotaError, decode_fmp_response, main  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._body


def test_decode_fmp_response_treats_http_429_as_quota_error():
    resp = FakeResponse(429, text='{"Error Message":"Limit Reach"}')

    with pytest.raises(FMPQuotaError):
        decode_fmp_response(resp)


def test_decode_fmp_response_treats_limit_body_as_quota_error():
    resp = FakeResponse(200, body={"Error Message": "Limit Reach . Please upgrade your plan"})

    with pytest.raises(FMPQuotaError):
        decode_fmp_response(resp)


def test_main_allows_quota_exhausted_skip(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", [
        "sync_fmp_earnings.py",
        "--allow-quota-exhausted",
        "--data-dir",
        str(tmp_path),
    ])
    monkeypatch.setattr("sync_fmp_earnings.load_local_env", lambda: None)
    monkeypatch.setattr("sync_fmp_earnings.get_api_key", lambda: "test-key")
    monkeypatch.setattr(
        "sync_fmp_earnings.fetch_fmp",
        lambda *args, **kwargs: (_ for _ in ()).throw(FMPQuotaError("FMP HTTP 429: limit")),
    )

    assert main() == 0
    out = capsys.readouterr().out
    assert "skipping FMP earnings overlay because quota is exhausted" in out

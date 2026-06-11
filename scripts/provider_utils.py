#!/usr/bin/env python3
"""Shared helpers for quota-managed financial data providers.

The provider enrichment scripts reserve credits before HTTP calls. This is
conservative: a transient network failure may under-use the daily allowance, but
reruns cannot accidentally run past a free/basic plan cap.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "provider_usage_ledger.json"


class ProviderQuotaError(RuntimeError):
    """Raised when a provider budget would be exceeded."""


def load_local_env() -> None:
    """Local dev convenience. Real environment variables still win."""
    for path in [
        REPO_ROOT / "config" / ".env.local",
        REPO_ROOT / "config" / ".env.production",
    ]:
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        return Path(raw)
    return REPO_ROOT / "data"


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_optional_int(name: str, default: int | None, *, minimum: int = 0) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    if raw.strip().lower() in {"none", "null", "unlimited"}:
        return None
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def fmp_api_key() -> str | None:
    return (
        os.getenv("FMP_API_KEY")
        or os.getenv("FINANCIAL_MODELING_PREP_API_KEY")
        or os.getenv("FINANCIALMODELINGPREP_API_KEY")
    )


def alpha_vantage_api_key() -> str | None:
    return os.getenv("ALPHAVANTAGE_API_KEY")


def massive_api_key() -> str | None:
    """Massive is the post-acquisition Polygon API surface.

    POLYGON_API_KEY is canonical in this repo. MASSIVE_API_KEY remains a local
    compatibility alias for older probe scripts and ad-hoc shells.
    """
    return os.getenv("POLYGON_API_KEY") or os.getenv("MASSIVE_API_KEY")


def twelvedata_api_key() -> str | None:
    return (
        os.getenv("TWELVEDATA_API_KEY")
        or os.getenv("TWELVE_DATA_API_KEY")
        or os.getenv("TWELVEDATA_KEY")
    )


# Env names that hold a provider's primary key, in priority order. Key-pool
# stacking also reads numbered variants of each (e.g. ALPHAVANTAGE_API_KEY_2).
_PROVIDER_KEY_ENV: dict[str, list[str]] = {
    "fmp": [
        "FMP_API_KEY",
        "FINANCIAL_MODELING_PREP_API_KEY",
        "FINANCIALMODELINGPREP_API_KEY",
    ],
    "alphavantage": ["ALPHAVANTAGE_API_KEY"],
    "massive": ["POLYGON_API_KEY", "MASSIVE_API_KEY"],
    "twelvedata": ["TWELVEDATA_API_KEY", "TWELVE_DATA_API_KEY", "TWELVEDATA_KEY"],
}

# Upper bound on stacked keys scanned per provider (KEY, KEY_2 .. KEY_N).
MAX_STACKED_KEYS = 8


def api_keys_for_provider(provider: str) -> list[str]:
    """All configured keys for a provider, ordered, de-duplicated.

    Reads the canonical key (and aliases) plus numbered stacking variants of
    each: ``ALPHAVANTAGE_API_KEY``, ``ALPHAVANTAGE_API_KEY_2``, ``..._3`` and so
    on. Stacking multiplies the usable free-tier budget — the usage ledger
    accounts for each key separately so the runner can fail over when one key's
    daily/minute cap is reached. Returns ``[]`` when none are configured.
    """
    names = _PROVIDER_KEY_ENV.get(provider)
    if names is None:
        raise ValueError(f"unknown provider: {provider}")
    keys: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            keys.append(value)

    for name in names:
        _add(os.getenv(name))
    for name in names:
        for idx in range(2, MAX_STACKED_KEYS + 1):
            _add(os.getenv(f"{name}_{idx}"))
    return keys


def api_key_for_provider(provider: str) -> str | None:
    """First configured key for a provider (single-key callers / probes)."""
    keys = api_keys_for_provider(provider)
    return keys[0] if keys else None


@dataclass(frozen=True)
class ProviderBudget:
    daily_limit: int | None
    minute_limit: int | None = None
    minute_window_sec: int = 60


def provider_budgets_from_env() -> dict[str, ProviderBudget]:
    return {
        "fmp": ProviderBudget(
            daily_limit=_env_optional_int("FMP_DAILY_CALL_LIMIT", 225, minimum=0),
        ),
        "alphavantage": ProviderBudget(
            daily_limit=_env_optional_int("ALPHAVANTAGE_DAILY_CALL_LIMIT", 25, minimum=0),
        ),
        "twelvedata": ProviderBudget(
            daily_limit=_env_optional_int("TWELVEDATA_DAILY_CREDIT_LIMIT", 792, minimum=0),
            minute_limit=_env_optional_int("TWELVEDATA_MINUTE_CREDIT_LIMIT", 8, minimum=1),
            minute_window_sec=_env_int("TWELVEDATA_MINUTE_WINDOW_SEC", 60, minimum=1),
        ),
        "massive": ProviderBudget(
            daily_limit=_env_optional_int("MASSIVE_DAILY_CALL_LIMIT", None, minimum=0),
            minute_limit=_env_optional_int("MASSIVE_MINUTE_CALL_LIMIT", 5, minimum=1),
            minute_window_sec=_env_int("MASSIVE_MINUTE_WINDOW_SEC", 60, minimum=1),
        ),
    }


class ProviderUsageLedger:
    def __init__(
        self,
        path: Path = DEFAULT_LEDGER_PATH,
        budgets: dict[str, ProviderBudget] | None = None,
        *,
        today_fn: Callable[[], date] = utc_today,
        now_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.budgets = budgets or provider_budgets_from_env()
        self.today_fn = today_fn
        self.now_fn = now_fn

    def _today_iso(self) -> str:
        return self.today_fn().isoformat()

    def _fresh_state(self) -> dict[str, Any]:
        return {"date": self._today_iso(), "providers": {}, "events": []}

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._fresh_state()
        try:
            state = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return self._fresh_state()
        if state.get("date") != self._today_iso():
            return self._fresh_state()
        if not isinstance(state.get("providers"), dict):
            state["providers"] = {}
        if not isinstance(state.get("events"), list):
            state["events"] = []
        return state

    def write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)

    def used(self, provider: str, account: str | None = None) -> int:
        providers = self.read().get("providers", {})
        info = providers.get(provider) if isinstance(providers, dict) else {}
        info = info or {}
        if account is None:
            return int(info.get("used", 0))
        accounts = info.get("accounts")
        if not isinstance(accounts, dict):
            return 0
        return int((accounts.get(account) or {}).get("used", 0))

    def remaining(self, provider: str) -> int | None:
        budget = self.budgets.get(provider)
        if not budget or budget.daily_limit is None:
            return None
        return max(0, budget.daily_limit - self.used(provider))

    def _minute_events(
        self,
        state: dict[str, Any],
        provider: str,
        now: datetime,
        account: str | None = None,
    ) -> list[dict[str, Any]]:
        budget = self.budgets.get(provider)
        if not budget or not budget.minute_limit:
            return []
        cutoff = now - timedelta(seconds=budget.minute_window_sec)
        out: list[dict[str, Any]] = []
        for event in state.get("events", []):
            if not isinstance(event, dict) or event.get("provider") != provider:
                continue
            if account is not None and event.get("account", "k0") != account:
                continue
            try:
                at = datetime.fromisoformat(str(event.get("at")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at >= cutoff:
                out.append(event)
        return out

    def seconds_until_available(
        self,
        provider: str,
        *,
        credits: int = 1,
        now: datetime | None = None,
        account: str | None = None,
    ) -> float:
        budget = self.budgets.get(provider)
        if not budget or not budget.minute_limit:
            return 0.0
        now = now or self.now_fn()
        events = self._minute_events(self.read(), provider, now, account=account)
        used = sum(int(e.get("credits", 1)) for e in events)
        if used + credits <= budget.minute_limit:
            return 0.0
        oldest_raw = min(str(e.get("at")) for e in events)
        try:
            oldest = datetime.fromisoformat(oldest_raw.replace("Z", "+00:00"))
        except ValueError:
            return float(budget.minute_window_sec)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        ready_at = oldest + timedelta(seconds=budget.minute_window_sec)
        return max(0.0, (ready_at - now).total_seconds() + 0.01)

    def reserve(
        self,
        provider: str,
        endpoint: str,
        *,
        credits: int = 1,
        symbols: list[str] | None = None,
        wait_for_minute: bool = False,
        sleep_fn: Callable[[float], None] = time.sleep,
        # "k0" matches reserve_pooled's first stacked key so single-key
        # callers and the pool share ONE bucket per physical key. A separate
        # "default" bucket let the same key be double-budgeted (seen 2026-06-10:
        # AV key #1 blocked at its real 25/day while the pool's k0 bucket still
        # showed headroom, so rotation to key #2 never happened).
        account: str = "k0",
    ) -> int:
        credits = max(0, int(credits))
        if credits == 0:
            return 0
        budget = self.budgets.get(provider, ProviderBudget(daily_limit=None))
        if budget.minute_limit and credits > budget.minute_limit:
            raise ProviderQuotaError(
                f"{provider} request needs {credits} credits, above minute cap {budget.minute_limit}"
            )

        while True:
            now = self.now_fn()
            wait_s = self.seconds_until_available(
                provider, credits=credits, now=now, account=account
            )
            if wait_s <= 0:
                break
            if not wait_for_minute:
                raise ProviderQuotaError(
                    f"{provider} minute budget exhausted; retry in {wait_s:.1f}s"
                )
            sleep_fn(wait_s)

        state = self.read()
        providers = state.setdefault("providers", {})
        info = providers.setdefault(provider, {"used": 0})
        accounts = info.setdefault("accounts", {})
        acct_info = accounts.setdefault(account, {"used": 0})
        acct_used = int(acct_info.get("used", 0))
        # Daily limit applies per key (per account). Stacking N keys gives
        # N * daily_limit total headroom.
        if budget.daily_limit is not None and acct_used + credits > budget.daily_limit:
            raise ProviderQuotaError(
                f"{provider}[{account}] daily budget exhausted: requested {credits}, "
                f"remaining {budget.daily_limit - acct_used}"
            )
        acct_info["used"] = acct_used + credits
        info["used"] = int(info.get("used", 0)) + credits
        info["daily_limit"] = budget.daily_limit
        info["minute_limit"] = budget.minute_limit
        event: dict[str, Any] = {
            "at": self.now_fn().isoformat(),
            "provider": provider,
            "endpoint": endpoint,
            "credits": credits,
            "symbols": (symbols or [])[:50],
        }
        if account != "k0":
            event["account"] = account
        state.setdefault("events", []).append(event)
        self.write(state)
        return credits

    def reserve_pooled(
        self,
        provider: str,
        endpoint: str,
        accounts: list[str],
        *,
        credits: int = 1,
        symbols: list[str] | None = None,
        wait_for_minute: bool = False,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> tuple[str, int]:
        """Reserve `credits` on the first stacked key with room; return its id.

        Tries accounts in order, preferring one with both daily and minute
        headroom right now. Falls back to the account with the soonest minute
        availability (waiting only when `wait_for_minute`). Raises
        ProviderQuotaError only when every key's daily budget is exhausted.
        """
        if not accounts:
            accounts = ["k0"]
        budget = self.budgets.get(provider, ProviderBudget(daily_limit=None))
        now = self.now_fn()

        daily_ok: list[str] = []
        for account in accounts:
            if (
                budget.daily_limit is None
                or self.used(provider, account) + credits <= budget.daily_limit
            ):
                daily_ok.append(account)
        # Balance load across keys instead of draining the first to its cap —
        # leaves headroom on each physical key for unledgered callers (e.g. the
        # AV V/OI probe hits key #1 directly) and provider-side miscounting.
        daily_ok.sort(key=lambda acct: self.used(provider, acct))
        if not daily_ok:
            raise ProviderQuotaError(
                f"{provider} daily budget exhausted across {len(accounts)} key(s)"
            )

        # Prefer a key that is also clear of its per-minute cap right now.
        for account in daily_ok:
            if self.seconds_until_available(
                provider, credits=credits, now=now, account=account
            ) <= 0:
                self.reserve(
                    provider,
                    endpoint,
                    credits=credits,
                    symbols=symbols,
                    wait_for_minute=wait_for_minute,
                    sleep_fn=sleep_fn,
                    account=account,
                )
                return account, credits

        # All daily-ok keys are minute-limited; pick the soonest to free up.
        best = min(
            daily_ok,
            key=lambda a: self.seconds_until_available(
                provider, credits=credits, now=now, account=a
            ),
        )
        self.reserve(
            provider,
            endpoint,
            credits=credits,
            symbols=symbols,
            wait_for_minute=wait_for_minute,
            sleep_fn=sleep_fn,
            account=best,
        )
        return best, credits


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)

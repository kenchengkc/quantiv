"""Keep market observations on their verified side of a price discontinuity."""

from __future__ import annotations

from datetime import date

try:
    from delisted import ticker_history_cutovers
except ModuleNotFoundError:
    from scripts.delisted import ticker_history_cutovers


def market_data_history_filter_sql(
    *, symbol: str = "act_symbol", observation_date: str = "date"
) -> str:
    """Filter raw rows without aliasing securities or inventing adjustments.

    A spin-off can leave provider backfills under the successor symbol and stale
    quotes under the retired symbol. Excluding those rows before feature/window
    calculations prevents a distributed security's value becoming a price move.
    Arguments are trusted SQL expressions; configuration values are literals.
    """
    clauses = []
    for old, (new, cutoff) in sorted(ticker_history_cutovers().items()):
        old_literal = "'" + old.replace("'", "''") + "'"
        new_literal = "'" + new.replace("'", "''") + "'"
        cutoff_literal = "DATE '" + cutoff.replace("'", "''") + "'"
        clauses.extend([
            f"({symbol} IS DISTINCT FROM {old_literal} "
            f"OR CAST({observation_date} AS DATE) < {cutoff_literal})",
            f"({symbol} IS DISTINCT FROM {new_literal} "
            f"OR CAST({observation_date} AS DATE) >= {cutoff_literal})",
        ])
    return " AND ".join(clauses) or "TRUE"


def market_data_window_is_comparable(symbol: str, start: date, end: date) -> bool:
    """Whether both endpoints belong to this symbol's verified price regime."""
    symbol = symbol.strip().upper()
    for old, (new, cutoff) in ticker_history_cutovers().items():
        boundary = date.fromisoformat(cutoff)
        if symbol == old and max(start, end) >= boundary:
            return False
        if symbol == new and min(start, end) < boundary:
            return False
    return True


def volatility_history_values_sql() -> str:
    """Typed vendor fields whose lookbacks cannot bridge a discontinuity.

    Current IV is observed in the current options market. Retrospective vendor
    windows and price adjustments are unverified, so successor summaries stay
    absent until their methodology/comparability is verified. Independently
    computed volatility from filtered OHLCV remains usable.
    """
    cutovers = ticker_history_cutovers()
    fields = []
    for name in (
        "hv_current", "hv_week_ago", "hv_month_ago", "hv_year_high", "hv_year_low",
        "iv_current", "iv_week_ago", "iv_month_ago", "iv_year_high", "iv_year_low",
    ):
        blocked = []
        if name != "iv_current":
            for new, _ in cutovers.values():
                condition = "act_symbol = '" + new.replace("'", "''") + "'"
                blocked.append(f"({condition})")
        value = f"CAST({name} AS DOUBLE)"
        if blocked:
            value = f"CASE WHEN {' OR '.join(blocked)} THEN NULL ELSE {value} END"
        fields.append(f"{value} AS {name}")
    return ",\n".join(fields)

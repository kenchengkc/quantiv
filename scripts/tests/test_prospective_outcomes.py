from datetime import date

import duckdb
import pandas as pd
import pytest


def connection():
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE v_earnings(act_symbol VARCHAR, date DATE, timing VARCHAR)"
    )
    conn.execute(
        "INSERT INTO v_earnings VALUES ('OPTIONLESS','2026-10-06','amc'), ('UNCOVERED','2026-10-06','amc'), ('REVISED','2026-10-08','bmo')"
    )
    conn.execute("CREATE TABLE v_ohlcv(act_symbol VARCHAR, date DATE, close DOUBLE)")
    conn.execute(
        "INSERT INTO v_ohlcv SELECT symbol,day,price FROM (VALUES ('OPTIONLESS'),('UNCOVERED'),('REVISED')) symbols(symbol), (VALUES (DATE '2026-10-06',100.0),(DATE '2026-10-07',110.0),(DATE '2026-10-08',121.0)) prices(day,price)"
    )
    conn.execute(
        "CREATE TABLE v_corporate_action_coverage(act_symbol VARCHAR, window_start DATE, window_end DATE)"
    )
    conn.execute(
        "INSERT INTO v_corporate_action_coverage VALUES ('OPTIONLESS','2019-01-01','2026-10-15'), ('REVISED','2019-01-01','2026-10-15')"
    )
    return conn


def observations():
    return pd.DataFrame(
        {
            "act_symbol": ["OPTIONLESS", "OPTIONLESS", "UNCOVERED", "REVISED"],
            "earnings_date": pd.to_datetime(["2026-10-06"] * 4),
            "timing": ["amc"] * 4,
            "model_horizon": [1, 7, 1, 1],
        }
    )


def test_optionless_outcomes_do_not_require_training_or_options_features():
    from scripts.prospective_outcomes import extract_prospective_labels

    with connection() as conn:
        labels = extract_prospective_labels(
            conn, observations(), as_of_date=date(2026, 10, 8)
        )
    assert labels["__symbol"].tolist() == ["OPTIONLESS"]
    assert labels["target"].tolist() == pytest.approx([0.1])
    assert labels["__target_protocol"].tolist() == ["quantiv.session-reaction.v2"]
    assert str(labels.iloc[0]["__label_available_at"])[:10] == "2026-10-07"


def test_outcomes_require_current_event_identity_and_verified_actions():
    from scripts.prospective_outcomes import extract_prospective_labels

    with connection() as conn:
        labels = extract_prospective_labels(
            conn, observations(), as_of_date=date(2026, 10, 8)
        )
    assert not {"UNCOVERED", "REVISED"}.intersection(labels["__symbol"])


def test_outcomes_wait_for_reaction_session_close():
    from scripts.prospective_outcomes import extract_prospective_labels

    with connection() as conn:
        labels = extract_prospective_labels(
            conn, observations(), as_of_date=date(2026, 10, 6)
        )
    assert labels.empty


def test_revised_ledger_timing_excludes_only_the_conflicting_event():
    from scripts.prospective_outcomes import extract_prospective_labels

    rows = pd.concat(
        [
            observations(),
            pd.DataFrame(
                {
                    "act_symbol": ["OPTIONLESS", "REVISED"],
                    "earnings_date": pd.to_datetime(["2026-10-06", "2026-10-08"]),
                    "timing": ["bmo", "bmo"],
                    "model_horizon": [2, 1],
                }
            ),
        ],
        ignore_index=True,
    )
    with connection() as conn:
        labels = extract_prospective_labels(conn, rows, as_of_date=date(2026, 10, 9))
    assert labels["__symbol"].tolist() == ["REVISED"]

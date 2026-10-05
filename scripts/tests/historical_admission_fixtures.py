"""Offline source receipts with the production corporate-action identities."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts import sync_dolthub


def write_action_receipt(data_dir: Path, *, source_date="2026-09-01", splits=None):
    options = data_dir / f"parquet/options_chain/year={source_date[:4]}/month={source_date[5:7]}/{source_date}.parquet"
    options.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"act_symbol": ["TEST"]}).to_parquet(options, index=False)
    frames = {
        "splits": pd.DataFrame(splits or [], columns=["act_symbol", "ex_date", "to_factor", "for_factor"]),
        "dividends": pd.DataFrame(columns=["act_symbol", "ex_date", "amount"]),
    }
    schemas = {"splits": sync_dolthub.SPLIT_SCHEMA, "dividends": sync_dolthub.DIVIDEND_SCHEMA}
    datasets = {}
    for name, frame in frames.items():
        if not frame.empty:
            frame["ex_date"] = pd.to_datetime(frame["ex_date"]).dt.date
        digest = sync_dolthub._action_content_digest(frame, list(frame))
        path = data_dir / f"parquet/corporate_actions/{name}/{digest}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, schema=schemas[name], preserve_index=False), path)
        datasets[name] = {
            "rows": len(frame), "partition": path.relative_to(data_dir).as_posix(),
            "partition_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_sha256": digest,
            "batches": [{"symbols": 1, "rows": len(frame), "pages": 1, "completion": "short_page"}],
        }
    receipt = {
        "schema": "quantiv.corporate-action-ingestion.v1",
        "source": "dolthub:post-no-preference/stocks",
        "source_options_date": source_date,
        "query_start": "2019-01-01", "query_end": source_date,
        "universe": {"symbols": 1, "symbols_sha256": hashlib.sha256(b"TEST").hexdigest(),
                     "method": "latest_options_partition_excluding_retired_symbols"},
        "datasets": datasets, "replay_equivalence": "verified", "revision": 1,
    }
    return publish_action_receipt(data_dir, receipt)


def publish_action_receipt(data_dir: Path, receipt: dict):
    receipt = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = sync_dolthub._json_digest(receipt)
    root = data_dir / "control/ingestion/corporate_actions"
    immutable = root / f"receipts/{receipt['source_options_date']}/{receipt['receipt_id']}.json"
    immutable.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    immutable.write_text(payload)
    (root / "latest.json").write_text(payload)
    return receipt

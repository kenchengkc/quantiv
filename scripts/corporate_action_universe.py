"""Reconstruct bounded action coverage from receipt-bound source snapshots.

Mutable calendar, forecast-ledger and ticker-policy bytes are preserved inside
content-addressed Parquet envelopes so the ordinary immutable data release also
authenticates them. Price/options partitions remain referenced byte-for-byte.
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
from typing import Callable

import pandas as pd


UNIVERSE_METHOD = "receipt_bound_options_and_price_backed_events_v1"
PROSPECTIVE_DAYS = 35
SNAPSHOT_ROOT = "parquet/corporate_actions/universe_sources"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def symbols_digest(symbols: list[str]) -> str:
    return hashlib.sha256("\n".join(symbols).encode()).hexdigest()


def invalid_action_values(table: str, row: dict) -> bool:
    """Unknown, nonfinite or nonphysical actions cannot establish issuer coverage."""
    columns = ("to_factor", "for_factor") if table == "split" else ("amount",)
    try:
        values = [float(row[column]) for column in columns]
        return any(not math.isfinite(value) or (value <= 0 if table == "split" else value < 0)
                   for value in values)
    except (KeyError, TypeError, ValueError, OverflowError):
        return True


def action_query(table: str, columns: list[str], symbols: list[str], start: date,
                 end: date, limit: int, last_key: tuple[str, str] | None = None) -> str:
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    keyset = (" AND (act_symbol, ex_date) > "
              f"({literal(last_key[0])}, {literal(last_key[1])})") if last_key else ""
    return (f"SELECT {', '.join(columns)} FROM {table} "
            f"WHERE act_symbol IN ({', '.join(literal(symbol) for symbol in symbols)}) "
            f"AND ex_date BETWEEN {literal(start.isoformat())} "
            f"AND {literal(end.isoformat())}{keyset} "
            f"ORDER BY act_symbol, ex_date LIMIT {limit}")


def retain_action_quarantine(data_dir: Path, source: dict, excluded: list[str]) -> dict:
    content = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    path = data_dir / f"parquet/corporate_actions/quarantine/{digest}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        pd.DataFrame({"source_bytes": [content]}).to_parquet(temporary, index=False)
        temporary.replace(path)
    if pd.read_parquet(path)["source_bytes"].tolist() != [content]:
        raise RuntimeError("action quarantine source snapshot collision")
    return {"schema": "quantiv.corporate-action-quarantine.v1",
            "partition": path.relative_to(data_dir).as_posix(),
            "partition_sha256": _file_digest(path), "source_sha256": digest,
            "excluded_symbols": excluded, "excluded_symbols_sha256": symbols_digest(excluded)}


def verify_action_quarantine(quarantine: dict, symbols: list[str], start: date, end: date,
                             release_member: Callable[[str], Path]) -> list[str]:
    """Replay complete raw source pages before deriving any excluded issuer."""
    if quarantine["schema"] != "quantiv.corporate-action-quarantine.v1":
        raise ValueError("unsupported action quarantine schema")
    digest = quarantine["source_sha256"]
    if quarantine["partition"] != f"parquet/corporate_actions/quarantine/{digest}.parquet":
        raise ValueError("action quarantine source is not content addressed")
    path = release_member(quarantine["partition"])
    if _file_digest(path) != quarantine["partition_sha256"]:
        raise ValueError("action quarantine partition digest mismatch")
    envelope = pd.read_parquet(path)
    if list(envelope) != ["source_bytes"] or len(envelope) != 1:
        raise ValueError("action quarantine envelope is malformed")
    content = envelope.iloc[0]["source_bytes"]
    if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("action quarantine raw source digest mismatch")
    source = json.loads(content)
    if (source["schema"] != "quantiv.corporate-action-source-pages.v1"
            or source["source"] != "dolthub:post-no-preference/stocks"
            or source["query_start"] != start.isoformat() or source["query_end"] != end.isoformat()
            or set(source["datasets"]) != {"splits", "dividends"}):
        raise ValueError("action quarantine source contract differs from coverage")
    excluded = set()
    for name, table, values in (("splits", "split", ["to_factor", "for_factor"]),
                                 ("dividends", "dividend", ["amount"])):
        dataset = source["datasets"][name]
        pages, batches = dataset["pages"], dataset["batches"]
        cursor = page_cursor = 0
        if not batches:
            raise ValueError("action quarantine source has no query completion proof")
        for index, batch in enumerate(batches):
            count, limit = batch["symbols"], batch["page_limit"]
            selected = symbols[cursor:cursor + count]
            sizes = batch["page_rows"]
            if (type(count) is not int or count < 1 or len(selected) != count
                    or batch["batch"] != index or batch["symbols_sha256"] != symbols_digest(selected)
                    or type(limit) is not int or not 1 <= limit <= 1000
                    or batch["completion"] != "short_page" or not sizes
                    or batch["pages"] != len(sizes) or batch["rows"] != sum(sizes)
                    or any(type(size) is not int or size != limit for size in sizes[:-1])
                    or type(sizes[-1]) is not int or not 0 <= sizes[-1] < limit):
                raise ValueError("action quarantine source pagination is incomplete")
            last_key = None
            for page_index, size in enumerate(sizes):
                page = pages[page_cursor]
                if (page["batch"] != index or page["page"] != page_index
                        or len(page["rows"]) != size
                        or page["query"] != action_query(table, ["act_symbol", "ex_date", *values],
                                                        selected, start, end, limit, last_key)):
                    raise ValueError("action quarantine source query or page differs from exhaustive scope")
                for row in page["rows"]:
                    key = (row["act_symbol"], date.fromisoformat(str(row["ex_date"])).isoformat())
                    if (key[0] not in selected or not start.isoformat() <= key[1] <= end.isoformat()
                            or (last_key is not None and key <= last_key)):
                        raise ValueError("action quarantine source row is out of scope or unordered")
                    if invalid_action_values(table, row):
                        excluded.add(key[0])
                    last_key = key
                page_cursor += 1
            cursor += count
        if cursor != len(symbols) or page_cursor != len(pages):
            raise ValueError("action quarantine source omits or duplicates symbols/pages")
    derived = sorted(excluded)
    if (not derived or quarantine["excluded_symbols"] != derived
            or quarantine["excluded_symbols_sha256"] != symbols_digest(derived)):
        raise ValueError("action quarantine exclusions differ from invalid raw provider observations")
    return derived


def _binding(data_dir: Path, source: Path, role: str, *, origin: str | None = None,
             snapshot: bool = False) -> dict:
    digest = _file_digest(source)
    origin = origin or source.relative_to(data_dir).as_posix()
    partition = source
    if snapshot:
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError("action universe source changed while being snapshotted")
        partition = data_dir / SNAPSHOT_ROOT / role / f"{digest}.parquet"
        partition.parent.mkdir(parents=True, exist_ok=True)
        if not partition.exists():
            temporary = partition.with_suffix(".tmp")
            pd.DataFrame({"source_bytes": [content]}).to_parquet(temporary, index=False)
            temporary.replace(partition)
        stored = pd.read_parquet(partition)
        if list(stored) != ["source_bytes"] or stored["source_bytes"].tolist() != [content]:
            raise RuntimeError("immutable action universe source snapshot collision")
    return {"role": role, "origin": origin,
            "partition": partition.relative_to(data_dir).as_posix(),
            "partition_sha256": _file_digest(partition),
            "source_sha256": digest}


def _source_content(data_dir: Path, binding: dict, source_date: date,
                    release_member: Callable[[str], Path]) -> bytes | Path:
    role, origin = binding["role"], binding["origin"]
    options = f"parquet/options_chain/year={source_date.year}/month={source_date.month:02d}/{source_date}.parquet"
    fixed = {"options": options, "calendar": "earnings_calendar.csv",
             "ledger": "forecasts/event_prediction_ledger.parquet",
             "delistings": "config/delisted_tickers.json", "renames": "config/ticker_renames.json"}
    if role == "prices":
        member = Path(origin)
        if not origin.startswith("parquet/ohlcv/") or member.suffix != ".parquet":
            raise ValueError("action universe price source origin is unsupported")
    elif fixed.get(role) != origin:
        raise ValueError("action universe source role or origin is unsupported")
    path = release_member(binding["partition"])
    partition_digest = _file_digest(path)
    if partition_digest != binding["partition_sha256"]:
        raise ValueError("action universe source partition digest mismatch")
    if role in {"options", "prices"}:
        if binding["partition"] != origin:
            raise ValueError("action universe immutable partition differs from source origin")
        if partition_digest != binding["source_sha256"]:
            raise ValueError("action universe original source digest mismatch")
        return path
    else:
        expected = f"{SNAPSHOT_ROOT}/{role}/{binding['source_sha256']}.parquet"
        if binding["partition"] != expected:
            raise ValueError("action universe source snapshot is not content addressed")
        envelope = pd.read_parquet(path)
        if list(envelope) != ["source_bytes"] or len(envelope) != 1:
            raise ValueError("action universe source envelope is invalid")
        content = envelope.iloc[0]["source_bytes"]
        if not isinstance(content, bytes):
            raise ValueError("action universe source envelope does not contain bytes")
    if hashlib.sha256(content).hexdigest() != binding["source_sha256"]:
        raise ValueError("action universe original source digest mismatch")
    return content


def reconstruct_universe(data_dir: Path, universe: dict, source_date: date,
                         start: date, end: date,
                         release_member: Callable[[str], Path]) -> tuple[list[str], dict[str, str]]:
    """Derive symbols from source observations; never trust a declared symbol list."""
    if universe["method"] != UNIVERSE_METHOD or universe["prospective_days"] != PROSPECTIVE_DAYS:
        raise ValueError("action universe derivation policy is unsupported")
    sources: dict[str, list[bytes | Path]] = {}
    identities = set()
    for binding in universe["sources"]:
        identity = (binding["role"], binding["origin"])
        if identity in identities:
            raise ValueError("action universe contains duplicate source bindings")
        identities.add(identity)
        sources.setdefault(binding["role"], []).append(
            _source_content(data_dir, binding, source_date, release_member))
    if any(len(sources.get(role, [])) != 1 for role in ("options", "delistings", "renames")):
        raise ValueError("action universe lacks its unique options or lifecycle source")
    if any(len(sources.get(role, [])) > 1 for role in ("calendar", "ledger")):
        raise ValueError("action universe has multiple mutable event sources")
    delistings = json.loads(sources["delistings"][0])["tickers"]
    renames = json.loads(sources["renames"][0])["renames"]
    if not isinstance(delistings, dict) or not isinstance(renames, dict):
        raise ValueError("action universe lifecycle policy is invalid")

    def normalized(value) -> str:
        return str(value).strip().upper() if pd.notna(value) else ""

    def canonical(value) -> str:
        symbol = normalized(value)
        renamed = renames.get(symbol, {})
        # Replay the captured policy: a spin-off's old price history needs
        # action evidence under its own symbol, independently of the successor.
        if (isinstance(renamed, dict)
                and renamed.get("history_policy") == "preserve_pre_transition"
                and renamed.get("price_history_continuous") is False):
            return symbol
        return normalized(renamed.get("new", symbol)) if isinstance(renamed, dict) else symbol

    retired = {normalized(symbol): spec for symbol, spec in delistings.items()}

    def retirement(symbol: str) -> date | None:
        try:
            return date.fromisoformat(retired[symbol]["delisted_on"])
        except (TypeError, KeyError, ValueError):
            return None

    options = pd.read_parquet(sources["options"][0], columns=["act_symbol"])
    selected = {canonical(symbol) for symbol in options["act_symbol"].dropna()
                if normalized(symbol) not in retired and canonical(symbol) not in retired} - {""}
    if not selected:
        raise ValueError("action universe options source has no active symbols")

    priced: set[str] = set()
    for content in sources.get("prices", []):
        frame = pd.read_parquet(content, columns=["act_symbol", "date", "close"])
        dates = pd.to_datetime(frame["date"], errors="raise").dt.date
        closes = pd.to_numeric(frame["close"], errors="raise")
        for raw, day, close in zip(frame["act_symbol"], dates, closes):
            symbol = canonical(raw)
            cutoff = retirement(symbol)
            if (symbol and pd.notna(day) and start <= day <= end
                    and pd.notna(close) and math.isfinite(close) and close > 0
                    and (symbol not in retired or (cutoff is not None and day < cutoff))):
                priced.add(symbol)

    for role, day_column in (("calendar", "date"), ("ledger", "earnings_date")):
        for content in sources.get(role, []):
            frame = (pd.read_csv(BytesIO(content), converters={"act_symbol": str}) if role == "calendar"
                     else pd.read_parquet(BytesIO(content), columns=["act_symbol", day_column]))
            dates = pd.to_datetime(frame[day_column], errors="raise").dt.date
            for raw, day in zip(frame["act_symbol"], dates):
                symbol = canonical(raw)
                if not symbol or pd.isna(day) or not start <= day <= end + timedelta(days=PROSPECTIVE_DAYS):
                    continue
                if symbol in retired:
                    cutoff = retirement(symbol)
                    if cutoff is None or day >= cutoff or day > end:
                        continue
                if day > end or symbol in priced:
                    selected.add(symbol)
    symbols = sorted(selected)
    window_ends = {symbol: min(end, retirement(symbol) - timedelta(days=1)).isoformat()
                   if symbol in retired else end.isoformat() for symbol in symbols}
    return symbols, window_ends


def snapshot_universe(data_dir: Path, source_date: date, start: date, end: date) -> tuple[dict, list[str]]:
    """Freeze mutable source bytes before the first provider action query."""
    options = data_dir / f"parquet/options_chain/year={source_date.year}/month={source_date.month:02d}/{source_date}.parquet"
    sources = [_binding(data_dir, options, "options")]
    repo = Path(__file__).resolve().parent.parent
    for role, filename in (("delistings", "delisted_tickers.json"), ("renames", "ticker_renames.json")):
        sources.append(_binding(data_dir, repo / "config" / filename, role,
                                origin=f"config/{filename}", snapshot=True))
    for role, relative in (("calendar", "earnings_calendar.csv"), ("ledger", "forecasts/event_prediction_ledger.parquet")):
        if (data_dir / relative).exists():
            sources.append(_binding(data_dir, data_dir / relative, role, snapshot=True))
    for path in sorted((data_dir / "parquet/ohlcv").rglob("*.parquet")):
        sources.append(_binding(data_dir, path, "prices"))
    universe = {"method": UNIVERSE_METHOD, "prospective_days": PROSPECTIVE_DAYS, "sources": sources}
    symbols, _ = reconstruct_universe(data_dir, universe, source_date, start, end, lambda relative: data_dir / relative)
    digest = symbols_digest(symbols)
    path = data_dir / f"parquet/corporate_actions/universes/{digest}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        pd.DataFrame({"act_symbol": symbols}).to_parquet(temporary, index=False)
        temporary.replace(path)
    if pd.read_parquet(path)["act_symbol"].tolist() != symbols:
        raise RuntimeError("immutable action universe symbol snapshot collision")
    universe.update({"symbols": len(symbols), "symbols_sha256": digest,
                     "snapshot": {"partition": path.relative_to(data_dir).as_posix(),
                                  "partition_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
    return universe, symbols


def verify_universe(data_dir: Path, universe: dict, source_date: date, start: date, end: date,
                    release_member: Callable[[str], Path]) -> tuple[list[str], dict[str, str]]:
    symbols, window_ends = reconstruct_universe(data_dir, universe, source_date, start, end, release_member)
    digest = symbols_digest(symbols)
    snapshot = universe["snapshot"]
    if (universe["symbols"] != len(symbols) or universe["symbols_sha256"] != digest
            or snapshot["partition"] != f"parquet/corporate_actions/universes/{digest}.parquet"):
        raise ValueError("action universe does not match its immutable source observations")
    path = release_member(snapshot["partition"])
    frame = pd.read_parquet(path)
    if (hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["partition_sha256"]
            or list(frame) != ["act_symbol"] or frame["act_symbol"].tolist() != symbols):
        raise ValueError("action universe symbol snapshot differs from source observations")
    return symbols, window_ends

"""Append-only raw event record and its Arrow schema (plan section 5.2).

Every message that arrives on the wire is recorded *exactly as received*, even
when the normalized state it produces is invalid, parser-errored or unknown.
Downstream stages decide usability from the derived state, never by dropping
the raw evidence.

Feed-clock caveat: the Shoonya feed documents ``ft`` only as feed time in Unix
epoch seconds. Nothing deeper about its clock semantics is verified. This module
therefore stores the raw string untouched next to a parsed UTC value, and never
substitutes local receive time for it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from jev_imrp.common.version import PARSER_VERSION, RAW_EVENT_SCHEMA_VERSION

#: Ingestion status of one recorded event.
INGEST_OK = "ok"
INGEST_PARSE_ERROR = "parse_error"
INGEST_UNKNOWN_TYPE = "unknown_message_type"
INGEST_NON_QUOTE = "non_quote_message"

RAW_EVENT_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("session_id", pa.string(), nullable=False),
        pa.field("connection_id", pa.string(), nullable=False),
        pa.field("subscription_generation", pa.int32(), nullable=False),
        pa.field("sequence", pa.int64(), nullable=False),
        pa.field("exchange", pa.string()),
        pa.field("token", pa.string()),
        pa.field("symbol_as_received", pa.string()),
        pa.field("message_type", pa.string(), nullable=False),
        pa.field("feed_timestamp_raw", pa.string()),
        pa.field("feed_timestamp_utc", pa.timestamp("us", tz="UTC")),
        pa.field("received_timestamp_utc", pa.timestamp("us", tz="UTC")),
        pa.field("received_monotonic_ns", pa.int64(), nullable=False),
        pa.field("raw_payload_json", pa.string(), nullable=False),
        pa.field("parser_version", pa.string(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("ingestion_status", pa.string(), nullable=False),
        pa.field("data_origin", pa.string(), nullable=False),
    ]
)


def new_event_id(session_id: str, generation: int, sequence: int) -> str:
    """Deterministic, collision-resistant id for one recorded event.

    Deterministic on purpose: replaying the same recorded sequence produces the
    same ids, so replay parity can be compared field by field (plan 5.6.10).
    """
    digest = hashlib.sha256(f"{session_id}|{generation}|{sequence}".encode()).hexdigest()
    return digest[:32]


def parse_feed_timestamp(raw: Any) -> datetime | None:
    """Parse a feed ``ft`` value into UTC, or return None when unusable.

    Only Unix epoch seconds (documented) are accepted. Returns None rather than
    guessing when the value is absent or non-numeric.
    """
    if raw is None or raw == "":
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@dataclass(frozen=True, slots=True)
class RawEvent:
    """One received message, exactly as received.

    Missing market fields are represented as absent keys in ``payload`` and as
    None in Parquet columns. They are never zero-filled (plan 5.2).
    """

    session_id: str
    connection_id: str
    subscription_generation: int
    sequence: int
    message_type: str
    payload: dict[str, Any]
    received_timestamp_utc: datetime
    received_monotonic_ns: int
    exchange: str | None = None
    token: str | None = None
    symbol_as_received: str | None = None
    feed_timestamp_raw: Any = None
    feed_timestamp_utc: datetime | None = None
    ingestion_status: str = INGEST_OK
    data_origin: str = "live"
    parser_version: str = PARSER_VERSION
    schema_version: str = RAW_EVENT_SCHEMA_VERSION
    event_id: str = ""
    errors: tuple[str, ...] = field(default=())

    def with_event_id(self) -> RawEvent:
        """Return a copy carrying its deterministic event_id."""
        return RawEvent(
            **{
                **{f: getattr(self, f) for f in self.__slots__},
                "event_id": new_event_id(
                    self.session_id, self.subscription_generation, self.sequence
                ),
            }
        )

    @property
    def raw_payload_json(self) -> str:
        """The payload serialized deterministically, byte-stable across replays."""
        return json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str)

    def to_row(self) -> dict[str, Any]:
        """Flatten to one Parquet row."""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "connection_id": self.connection_id,
            "subscription_generation": self.subscription_generation,
            "sequence": self.sequence,
            "exchange": self.exchange,
            "token": self.token,
            "symbol_as_received": self.symbol_as_received,
            "message_type": self.message_type,
            "feed_timestamp_raw": (
                None if self.feed_timestamp_raw is None else str(self.feed_timestamp_raw)
            ),
            "feed_timestamp_utc": self.feed_timestamp_utc,
            "received_timestamp_utc": self.received_timestamp_utc,
            "received_monotonic_ns": self.received_monotonic_ns,
            "raw_payload_json": self.raw_payload_json,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "ingestion_status": self.ingestion_status,
            "data_origin": self.data_origin,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RawEvent:
        """Rebuild a RawEvent from a Parquet row (replay path)."""
        payload = json.loads(row["raw_payload_json"])
        return cls(
            event_id=row["event_id"],
            session_id=row["session_id"],
            connection_id=row["connection_id"],
            subscription_generation=int(row["subscription_generation"]),
            sequence=int(row["sequence"]),
            message_type=row["message_type"],
            payload=payload,
            received_timestamp_utc=row["received_timestamp_utc"],
            received_monotonic_ns=int(row["received_monotonic_ns"]),
            exchange=row["exchange"],
            token=row["token"],
            symbol_as_received=row["symbol_as_received"],
            feed_timestamp_raw=row["feed_timestamp_raw"],
            feed_timestamp_utc=row["feed_timestamp_utc"],
            ingestion_status=row["ingestion_status"],
            data_origin=row["data_origin"],
            parser_version=row["parser_version"],
            schema_version=row["schema_version"],
        )


def empty_table() -> pa.Table:
    """An empty table with the raw-event schema (typed, append-only friendly)."""
    return RAW_EVENT_SCHEMA.empty_table()


def table_from_events(events: list[RawEvent]) -> pa.Table:
    """Build a typed table from recorded events."""
    if not events:
        return empty_table()
    return pa.Table.from_pylist([e.to_row() for e in events], schema=RAW_EVENT_SCHEMA)

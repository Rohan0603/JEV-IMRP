"""Encoder for synthetic FYERS data-socket frames.

The encoder is the inverse of :mod:`jev_imrp.ingest.fyers_socket` and exists so
tests can build wire-accurate frames without a live socket or credentials.
Everything it emits is labelled synthetic by the fixture files.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime

from jev_imrp.ingest.fyers_socket import (
    ABSENT,
    BLOCK_FULL,
    BLOCK_LITE,
    BLOCK_SNAPSHOT,
    DP_FIELDS,
    IF_FIELDS,
    RESP_AUTH,
    RESP_DATA,
    RESP_SUBSCRIBE,
    SF_FIELDS,
)

#: Synthetic feed instant, 09:20 IST on a weekday, inside the continuous session.
SOURCE_TIME = datetime(2026, 7, 1, 3, 50, tzinfo=UTC)
SOURCE_EPOCH = int(SOURCE_TIME.timestamp())

SF_TOPIC = "sf|nse_cm|SYNTH2885"
SF_TOPIC_ID = 101
SBIN_TOPIC = "sf|nse_cm|SYNTH3045"
SBIN_TOPIC_ID = 102
INDEX_TOPIC = "if|nse_cm|NIFTY50-INDEX"
INDEX_TOPIC_ID = 103
DP_TOPIC = "dp|nse_cm|SYNTH2885"
DP_TOPIC_ID = 104


def _string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return bytes([len(raw)]) + raw


def _i32(value: int) -> bytes:
    return struct.pack("!i", value)


def frame(response_type: int, payload: bytes = b"") -> bytes:
    body = bytes([response_type]) + payload
    return struct.pack("!H", len(body)) + body


def control_frame(response_type: int, fields: tuple[tuple[int, bytes], ...] = ()) -> bytes:
    payload = bytes([len(fields)])
    for field_id, value in fields:
        payload += bytes([field_id]) + struct.pack("!H", len(value)) + value
    return frame(response_type, payload)


def data_frame(message_number: int, blocks: list[bytes]) -> bytes:
    payload = struct.pack("!IH", message_number, len(blocks)) + b"".join(blocks)
    return frame(RESP_DATA, payload)


def snapshot_block(
    topic_id: int,
    topic_name: str,
    values: list[int],
    *,
    multiplier: int = 1,
    precision: int = 2,
    exchange: str = "NSE",
    exchange_token: str = "SYNTH",
    symbol: str = "",
) -> bytes:
    block = bytes([BLOCK_SNAPSHOT]) + struct.pack("<H", topic_id) + _string(topic_name)
    block += bytes([len(values)]) + b"".join(_i32(value) for value in values)
    block += b"\x00\x00" + struct.pack("!H", multiplier) + bytes([precision])
    block += _string(exchange) + _string(exchange_token) + _string(symbol)
    return block


def full_block(topic_id: int, values: list[int]) -> bytes:
    block = bytes([BLOCK_FULL]) + struct.pack("<H", topic_id) + bytes([len(values)])
    return block + b"".join(_i32(value) for value in values)


def lite_block(topic_id: int, ltp: int) -> bytes:
    return bytes([BLOCK_LITE]) + struct.pack("<H", topic_id) + _i32(ltp)


def sf_values(**overrides: int) -> list[int]:
    """Full ``sf`` snapshot vector, raw (unscaled) integers."""
    base = {
        "ltp": 250_000,
        "vol_traded_today": 123_456,
        "last_traded_time": SOURCE_EPOCH,
        "exch_feed_time": SOURCE_EPOCH,
        "bid_size": 10,
        "ask_size": 12,
        "bid_price": 249_950,
        "ask_price": 250_050,
        "last_traded_qty": 5,
        "tot_buy_qty": 1_000,
        "tot_sell_qty": 1_200,
        "avg_trade_price": 250_000,
        "OI": 0,
        "low_price": 245_000,
        "high_price": 255_000,
        "Yhigh": 260_000,
        "Ylow": 240_000,
        "lower_ckt": 225_000,
        "upper_ckt": 275_000,
        "open_price": 248_000,
        "prev_close_price": 247_500,
    }
    base.update(overrides)
    return [base[name] for name in SF_FIELDS]


def if_values(**overrides: int) -> list[int]:
    base = {
        "ltp": 2_200_000,
        "prev_close_price": 2_195_000,
        "exch_feed_time": SOURCE_EPOCH,
        "high_price": 2_210_000,
        "low_price": 2_185_000,
        "open_price": 2_190_000,
    }
    base.update(overrides)
    return [base[name] for name in IF_FIELDS]


def dp_values(**overrides: int) -> list[int]:
    base = {
        "bid_price1": 249_950,
        "ask_price1": 250_050,
        "bid_price2": 249_900,
        "ask_price2": 250_100,
        "bid_price3": 249_850,
        "ask_price3": 250_150,
        "bid_price4": 249_800,
        "ask_price4": 250_200,
        "bid_price5": 249_750,
        "ask_price5": 250_250,
        "bid_size1": 10,
        "ask_size1": 12,
        "bid_size2": 20,
        "ask_size2": 22,
        "bid_size3": 30,
        "ask_size3": 32,
        "bid_size4": 40,
        "ask_size4": 42,
        "bid_size5": 50,
        "ask_size5": 52,
        "bid_order1": 3,
        "ask_order1": 2,
        "bid_order2": 4,
        "ask_order2": 3,
        "bid_order3": 5,
        "ask_order3": 4,
        "bid_order4": 6,
        "ask_order4": 5,
        "bid_order5": 7,
        "ask_order5": 6,
    }
    base.update(overrides)
    return [base[name] for name in DP_FIELDS]


def only(**overrides: int) -> list[int]:
    """Incremental vector: every field sentinel except the named ones."""
    base = dict.fromkeys(SF_FIELDS, ABSENT)
    base.update(overrides)
    return [base[name] for name in SF_FIELDS]


def only_dp(**overrides: int) -> list[int]:
    base = dict.fromkeys(DP_FIELDS, ABSENT)
    base.update(overrides)
    return [base[name] for name in DP_FIELDS]


def auth_ok() -> bytes:
    return control_frame(RESP_AUTH, ((1, b"K"), (2, b"1"), (3, b"1")))


def subscribe_ok() -> bytes:
    return control_frame(RESP_SUBSCRIBE, ((1, b"K"), (2, b"1"), (3, b"1")))

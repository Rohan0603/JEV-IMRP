# FYERS v3 Data Socket: offline qualification

Status: **offline only**. No live capture, no credentials, no orders. Every
fixture is hand-built and labelled `data_origin: synthetic`.

Scope: ORC-65 (parent ORC-107, Shoonya→FYERS migration). This document records
what the FYERS v3 data socket actually puts on the wire, how this repository
interprets it, and what is still unverified.

## Sources

| Source | Use |
| --- | --- |
| `fyers_apiv3==3.2.1` (`FyersWebsocket/data_ws.py`, `defines.py`, `map.json`) | Readable reference implementation: frame layout, field vectors, reconnect behaviour |
| FYERS v3 WebSocket docs | Endpoint, documented epoch-seconds clock, snapshot-before-incremental contract |
| `fyers-api-v3@2.4.1` (npm) | Minified; could not resolve topic-id byte order |

## Wire format

- Endpoint `wss://socket.fyers.in/hsm/v1-5/prod`, binary frames.
- Outer frame: `uint16` big-endian length (bytes after the length field),
  `uint8` response type.

| Type | Name | Meaning |
| --- | --- | --- |
| 1 | `cn` | auth |
| 4 | `sub` | subscribe ack |
| 5 | `unsub` | unsubscribe ack |
| 6 | `data` | market data |
| 7 | `cp` | pause |
| 8 | `cr` | resume |
| 12 | `mode` | mode change |
| 51 | `msg` | custom message |

Data frame: `uint32` big-endian message number, `uint16` big-endian scrip
count, then scrip blocks.

| Block | Kind | Body |
| --- | --- | --- |
| 83 | snapshot | topic id, topic name, `field_count`, `int32` vector, 2 reserved bytes, `uint16` multiplier, `uint8` precision, exchange, token, symbol |
| 85 | full/incremental | topic id, `field_count`, `int32` vector |
| 76 | lite | topic id, `int32` ltp |

- `INT32_MIN` (`-2147483648`) means **absent/unchanged**, never zero.
- Prices: `raw / (10**precision * multiplier)`. Sizes, quantities, order counts
  and timestamps are not scaled.
- Full and lite blocks carry only the topic id, so a snapshot must arrive first.
  A snapshot arrives on subscribe and after every reconnect.

## Quote clock

| Topic | Source time fields | Notes |
| --- | --- | --- |
| `sf` (symbol) | `exch_feed_time`, then `last_traded_time` | `last_traded_time` can stand still without trades, so it is a fallback |
| `if` (index) | `exch_feed_time` | |
| `dp` (depth) | none | Depth frames carry **no** source timestamp |

Policy (`parse_source_time`):

- The documented unit is Unix epoch **seconds**. The field is `int32`, so a
  millisecond epoch cannot physically be transmitted; the only realistic
  corruption is a small bogus value.
- A value outside 2000-01-01..2100-01-01 is **not** reinterpreted as ms/µs and
  local time is never substituted. The raw integer is kept, `source_time_utc`
  is `None`, and `source_clock_implausible` is reported.
- Freshness uses the **monotonic receipt clock** (`time.monotonic_ns`), so a
  wall-clock jump cannot make a quote look fresh or stale.
- Session bounds use `jev_imrp.common.timeutils.is_within_session`
  (09:15–15:30 IST). `outside_session` is reported as soft evidence.

## Reconnect and resubscription

The reference SDK clears its topic maps and re-runs `on_connect` on reconnect.
The server persists no subscription, so the client must re-send **every**
subscription, and each topic needs a fresh snapshot before it is trusted.

`FyersQuoteBook` models this with generations:

1. `begin_generation()` invalidates every state (`awaiting_resubscription_snapshot`).
2. `plan_resubscription()` returns every subscribed topic.
3. `awaiting_snapshot()` lists topics still missing a trusted snapshot.
4. The first snapshot in the new generation restores that topic.

## L1 availability

| Topic | Bid | Ask | Source time |
| --- | --- | --- | --- |
| `sf` | `bid_price` / `bid_size` | `ask_price` / `ask_size` | yes |
| `dp` | `bid_price1` / `bid_size1` | `ask_price1` / `ask_size1` | **no** |
| `if` | none | none | yes |

`FyersQuoteState.bid_price_1` / `ask_price_1` / `bid_qty_1` / `ask_qty_1`
prefer depth level 1 and fall back to the `sf` top of book.

## Reason codes

Hard (state is not valid): `crossed_quote`, `non_positive_price`,
`missing_size`, `missing_price`, `price_off_tick`, `feed_timestamp_regression`,
`awaiting_snapshot`, `awaiting_resubscription_snapshot`.

Soft (reported, state still valid): `locked_quote`, `source_clock_implausible`,
`outside_session`.

Reported separately, not stored as state reasons:

- `stale_quote` — returned by `FyersQuoteBook.stale_keys(now, max_age)`.
- `duplicate_update` — counted per topic in `FyersQuoteBook.duplicate_updates`.
- `parser_error` / `unknown_topic_id` — `FyersPacket.parse_error` plus a
  `connection_anomalies` entry; the state is left untouched.

## Evidence

- `tests/fixtures/fyers_socket/sanitized_packets.json` — hex frames, synthetic
  label, per-frame receive timestamps and generations.
- `tests/fixtures/fyers_socket/manifest.json` — protocol summary, clock policy,
  L1 availability, reconnect evidence, measured packet rate, SHA-256 of the
  packet file, and every `NOT RUN` item.
- `tests/fyers_packets.py` — the frame encoder used to build the fixtures.
- `tests/test_fyers_socket.py` — 27 offline tests.

Packet rate is measured from the synthetic replay only
(`measure_packet_rate`), because rate depends on subscription count and market
activity. `live_measurement` is `NOT RUN`.

## Usage

```python
import time

from jev_imrp.common.timeutils import utc_now
from jev_imrp.ingest.fyers_socket import FyersPacketDecoder, FyersQuoteBook

decoder = FyersPacketDecoder()
book = FyersQuoteBook(tick_sizes={"RELIANCE": 0.05})
book.register_subscription("sf|nse_cm|SYNTH2885", "RELIANCE")

for raw_frame in recorded_frames:  # bytes, never a live socket here
    now = utc_now()
    for packet in decoder.decode(
        raw_frame,
        received_timestamp_utc=now,
        received_monotonic_ns=time.monotonic_ns(),
        generation=book.generation,
        data_origin="live",
    ):
        book.apply(packet)
```

## Open items

- **NOT RUN**: live capture, live clock-unit verification, live packet-rate
  measurement. Requires credentials and separate authorization.
- **OPEN**: topic-id byte order. Decoded little-endian to match
  `fyers_apiv3==3.2.1` on x86; the Node SDK is minified and the JS docs body is
  not fetchable. Correlation is unaffected because snapshot and incremental
  frames are decoded the same way.
- Not implemented here: OAuth, history, symbol master, live transport,
  order placement. Those modules do not exist in the repository yet.

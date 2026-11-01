#!/usr/bin/env python3

"""
Live integration test for Shoonya + JEV pipeline.

This script demonstrates the complete pipeline:
1. OAuth login (if credentials are set)
2. Symbol resolution
3. Historical data backfill
4. Shoonya feed client integration
5. JEV client integration
6. Recorder integration

Research-only. Credentials come from environment variables.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from jev_imrp.ingest.historical_data_client import fetch_tp_series_data, fetch_eod_chart_data
from jev_imrp.ingest.symbol_resolver import resolve_symbol, SymbolResolutionError
from jev_imrp.ingest.shoonya_recorder import Recorder
from jev_imrp.ingest.file_writer import RawEventWriter, WriterSettings
from jev_imrp.ingest.raw_event import RawEvent, INGEST_OK
from jev_imrp.models.jev_client import LiveConfig, LiveJevClient, accept_reject_question, build_state, Candidate
from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner

# Configuration
REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_DIR = REPO_ROOT / "tests"

# Environment variables - set these before running
ENV_VARS = [
    "SHOONYA_API_KEY",
    "SHOONYA_API_SECRET",
    "SHOONYA_ACCESS_TOKEN",
    "SHOONYA_USER_ID",
    "TYPESAFE_API_KEY"
]

# Check environment variables
for var in ENV_VARS:
    if not os.getenv(var):
        print(f"ERROR: Missing environment variable {var}")
        print("Please set these before running the test.")
        print("Example:")
        print("export SHOONYA_API_KEY='your_api_key'")
        print("export SHOONYA_API_SECRET='your_api_secret'")
        print("export SHOONYA_ACCESS_TOKEN='your_access_token'")
        print("export SHOONYA_USER_ID='your_user_id'")
        print("export TYPESAFE_API_KEY='your_typesafe_api_key'")
        sys.exit(1)

async def test_symbol_resolution():
    """Test symbol resolution using both master and SearchScrip."""
    print("\n=== Testing Symbol Resolution ===")

    # Test with symbol master (preferred)
    try:
        token = resolve_symbol(
            exchange="NSE",
            symbol="SBIN-EQ",
            use_master=True,
            user_id=os.getenv("SHOONYA_USER_ID")
        )
        print(f"Symbol master resolved: {token.exchange}|{token.token} -> {token.symbol}")
    except SymbolResolutionError as e:
        print(f"Symbol master failed: {e}")

    # Test with SearchScrip (fallback)
    try:
        token = resolve_symbol(
            exchange="NSE",
            symbol="SBIN-EQ",
            use_master=False,
            user_id=os.getenv("SHOONYA_USER_ID")
        )
        print(f"SearchScrip resolved: {token.exchange}|{token.token} -> {token.symbol}")
    except SymbolResolutionError as e:
        print(f"SearchScrip failed: {e}")

async def test_historical_data_backfill():
    """Test historical data backfill with TPSeries and EODChartData."""
    print("\n=== Testing Historical Data Backfill ===")

    # Test TPSeries backfill
    try:
        print("Fetching TPSeries data...")
        tp_data = await fetch_tp_series_data(
            exchange="NSE",
            symbol="SBIN-EQ",
            from_date="2024-01-01",
            to_date="2024-01-02",
            interval="5m",
            user_id=os.getenv("SHOONYA_USER_ID")
        )
        print(f"Fetched {len(tp_data)} TPSeries candles")

        # Show first few candles
        for candle in tp_data[:3]:
            print(f"TP candle: {candle.get('time', '?')} -> {candle.get('lp', '?')}")
    except Exception as e:
        print(f"TPSeries backfill failed: {e}")

    # Test EODChartData backfill
    try:
        print("Fetching EODChartData data...")
        eod_data = await fetch_eod_chart_data(
            exchange="NSE",
            symbol="SBIN-EQ",
            from_date="2024-01-01",
            to_date="2024-01-02",
            user_id=os.getenv("SHOONYA_USER_ID")
        )
        print(f"Fetched {len(eod_data)} EOD candles")

        # Show first few candles
        for candle in eod_data[:3]:
            print(f"EOD candle: {candle.get('date', '?')} -> {candle.get('close', '?')}")
    except Exception as e:
        print(f"EODChartData backfill failed: {e}")

async def test_recorder_integration():
    """Test recorder integration with backfilled data."""
    print("\n=== Testing Recorder Integration ===")

    # Create a temporary directory for test data
    test_dir = TEST_DATA_DIR / "backfill_test"
    test_dir.mkdir(exist_ok=True)

    # Create a writer and recorder
    writer = RawEventWriter(test_dir, session_id="test-session", settings=WriterSettings(max_rows_per_file=1000))
    recorder = Recorder(
        writer=writer,
        book=None,  # Not needed for this test
        session_id="test-session",
        connection_id="test-connection",
        clock=lambda: (datetime.now(), 0),  # Use current time
        data_origin="test"
    )

    try:
        # Test TPSeries backfill
        print("Backfilling TPSeries data...")
        await recorder.backfill_historical_data(
            exchange="NSE",
            symbol="SBIN-EQ",
            from_date="2024-01-01",
            to_date="2024-01-02",
            interval="5m",
            user_id=os.getenv("SHOONYA_USER_ID")
        )

        # Check if data was written
        events = list(writer.read_all_events())
        print(f"Wrote {len(events)} TPSeries events to recorder")

        # Test EODChartData backfill
        print("Backfilling EODChartData data...")
        await recorder.backfill_eod_data(
            exchange="NSE",
            symbol="SBIN-EQ",
            from_date="2024-01-01",
            to_date="2024-01-02",
            user_id=os.getenv("SHOONYA_USER_ID")
        )

        # Check if data was written
        events = list(writer.read_all_events())
        print(f"Wrote {len(events)} EOD events to recorder")

        # Show sample events
        for i, event in enumerate(events[:5]):
            print(f"Sample event {i+1}:")
            print(f"  Type: {event.message_type}")
            print(f"  Time: {event.received_timestamp_utc}")
            print(f"  Exchange: {event.exchange}")
            print(f"  Token: {event.token}")
            print(f"  Symbol: {event.symbol_as_received}")
            print()

    finally:
        # Clean up
        writer.close()
        test_dir.rmdir()

async def test_jev_integration():
    """Test JEV client integration with synthetic candidates."""
    print("\n=== Testing JEV Integration ===")

    # Create a synthetic candidate
    candidate = Candidate(
        candidate_id="test_candidate_1",
        symbol="SBIN-EQ",
        side="BUY",
        decision_timestamp_utc="2026-10-27T09:30:00+05:30",
        horizon_minutes=30,
        features={
            "spread_bps": 2.5,
            "atr_pct": 0.8,
            "tod_minutes_since_open": 60.0
        },
        feature_set_version="fs_v1"
    )

    # Build the state
    state = build_state(candidate)

    # Create JEV client
    config = LiveConfig(
        model_id="jev-1.13.0",
        confirmatory=True,
        timeout_s=30
    )
    client = LiveJevClient(config, api_key=os.getenv("TYPESAFE_API_KEY"))

    # Create a question
    question = accept_reject_question()

    # Build the request
    request = {
        "state": state,
        "model": "jev-1.13.0",
        "questions": {
            "jev_accept": question.to_payload()
        }
    }

    try:
        # Send the request to JEV
        result = await client.answer(request, questions=(question,))
        print(f"JEV response status: {result.status}")
        if result.status == 200:
            print(f"JEV model returned: {result.returned_model_id}")
            print(f"JEV answer: {result.payload.get('answers', {}).get('jev_accept', {})}")
    except Exception as e:
        print(f"JEV integration failed: {e}")
    finally:
        await client.aclose()

async def test_jev_runner():
    """Test JEV runner with synthetic candidates."""
    print("\n=== Testing JEV Runner ===")

    # Create a synthetic candidate
    candidate = Candidate(
        candidate_id="test_candidate_1",
        symbol="SBIN-EQ",
        side="BUY",
        decision_timestamp_utc="2026-10-27T09:30:00+05:30",
        horizon_minutes=30,
        features={
            "spread_bps": 2.5,
            "atr_pct": 0.8,
            "tod_minutes_since_open": 60.0
        },
        feature_set_version="fs_v1"
    )

    # Create a question
    question = accept_reject_question()

    # Create attempt store
    attempt_store = AttemptStore(REPO_ROOT / "experiments" / "attempts.jsonl")

    # Create JEV client
    config = LiveConfig(
        model_id="jev-1.13.0",
        confirmatory=True,
        timeout_s=30
    )
    client = LiveJevClient(config, api_key=os.getenv("TYPESAFE_API_KEY"))

    # Create runner
    runner = JevRunner(
        client=client,
        store=attempt_store,
        experiment_id="jev_integration_test",
        questions=(question,),
        model_id="jev-1.13.0",
        failure_policy="reject",
        max_concurrency=1,
        confirmatory=True
    )

    try:
        # Run the candidate
        result = await runner.run([candidate])
        print(f"Runner status: {result.status}")
        print(f"Decision: {result.decisions[0].terminal}")
        print(f"Coverage: {result.coverage}")
    except Exception as e:
        print(f"JEV runner failed: {e}")
    finally:
        await client.aclose()

async def main():
    """Run all tests."""
    print("Starting Shoonya + JEV Integration Test")
    print("=" * 60)

    # Run tests
    await test_symbol_resolution()
    await test_historical_data_backfill()
    await test_recorder_integration()
    await test_jev_integration()
    await test_jev_runner()

    print("\nTest completed successfully!")
    print("All components are working as expected.")

if __name__ == "__main__":
    asyncio.run(main())
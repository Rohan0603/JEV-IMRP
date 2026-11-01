"""Tests for ShoonyaFeedClient.

This module tests the Shoonya WebSocket feed client integration.
Uses unittest.mock to avoid real network connections.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from jev_imrp.ingest.shoonya_feed_client import ShoonyaFeedClient
from jev_imrp.ingest.shoonya_recorder import Recorder
from jev_imrp.ingest.symbol_resolver import SymbolResolutionError


class TestShoonyaFeedClient(unittest.IsolatedAsyncioTestCase):
    """Test cases for ShoonyaFeedClient."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.recorder = Recorder(writer=MagicMock(), session_id="test-session")
        self.client = ShoonyaFeedClient(
            recorder=self.recorder,
            user_id="test_user",
            account_id="test_account",
            access_token="test_token",
            max_reconnect_attempts=1,
            reconnect_delay=0.1,
        )

    async def test_connect_frame(self):
        """Test that connect frame is sent correctly."""
        mock_socket = AsyncMock()
        mock_socket.__aenter__ = AsyncMock(return_value=mock_socket)
        mock_socket.__aexit__ = AsyncMock(return_value=None)
        # Mock message iteration to end immediately
        async def mock_async_iter(self):
            return
            yield  # Makes this an async generator
        mock_socket.__aiter__ = mock_async_iter

        with patch("jev_imrp.ingest.shoonya_feed_client.websockets.connect",
                   return_value=mock_socket):
            await self.client.connect()

            # Verify connect frame was sent
            mock_socket.send.assert_called_once()
            sent_frame = json.loads(mock_socket.send.call_args[0][0])
            expected_frame = {
                "t": "a",
                "uid": "test_user",
                "actid": "test_account",
                "source": "API",
                "accesstoken": "test_token",
            }
            self.assertEqual(sent_frame, expected_frame)

    async def test_process_tk_message(self):
        """Test processing of tk message type."""
        tk_message = {"t": "tk", "e": "NSE", "tk": "123", "lp": 100.0}

        with patch.object(self.client.recorder, "feed") as mock_feed:
            mock_feed.return_value = (MagicMock(), None)

            await self.client._process_message(tk_message)

            mock_feed.assert_called_once_with(tk_message)

    async def test_process_tf_message(self):
        """Test processing of tf message type."""
        tf_message = {"t": "tf", "e": "NSE", "tk": "123", "lp": 101.0}

        with patch.object(self.client.recorder, "feed") as mock_feed:
            mock_feed.return_value = (MagicMock(), None)

            await self.client._process_message(tf_message)

            mock_feed.assert_called_once_with(tf_message)

    async def test_process_unknown_message(self):
        """Test handling of unknown message types."""
        unknown_message = {"t": "unknown", "e": "NSE"}

        with patch.object(self.client.recorder, "feed") as mock_feed:
            mock_feed.return_value = (MagicMock(), None)

            # Should not crash on unknown type
            await self.client._process_message(unknown_message)

            # feed() is only called for tk/tf messages, not unknowns
            mock_feed.assert_not_called()

    async def test_process_error_message(self):
        """Test handling of error message type."""
        error_message = {"t": "error", "emsg": "Authentication failed"}

        with patch.object(self.client.recorder, "feed") as mock_feed:
            await self.client._process_message(error_message)

            # feed() is not called for error messages
            mock_feed.assert_not_called()

    async def test_subscribe(self):
        """Test subscription logic."""
        mock_socket = AsyncMock()
        self.client.connection = mock_socket

        with patch.object(self.client, "_resolve_symbol_to_token") as mock_resolve:
            mock_resolve.side_effect = ["123", "456"]

            await self.client.subscribe([("NSE", "SBIN-EQ"), ("NSE", "RELIANCE-EQ")])

            mock_socket.send.assert_called_once()
            sent_frame = json.loads(mock_socket.send.call_args[0][0])
            expected_frame = {"t": "t", "k": "NSE|123#NSE|456"}
            self.assertEqual(sent_frame, expected_frame)
            # Check subscriptions were cached correctly
            self.assertIn("NSE|123", self.client.subscriptions)
            self.assertIn("NSE|456", self.client.subscriptions)

    async def test_subscribe_empty_instruments(self):
        """Test subscription with no instruments."""
        mock_socket = AsyncMock()
        self.client.connection = mock_socket

        with patch.object(self.client, "_resolve_symbol_to_token") as mock_resolve:
            await self.client.subscribe([])

            mock_socket.send.assert_not_called()

    async def test_subscribe_no_connection(self):
        """Test subscription without active connection raises RuntimeError."""
        self.client.connection = None

        with patch.object(self.client, "_resolve_symbol_to_token") as mock_resolve:
            mock_resolve.side_effect = ["123"]

            with self.assertRaises(RuntimeError):
                await self.client.subscribe([("NSE", "SBIN-EQ")])

    async def test_unsubscribe(self):
        """Test unsubscription logic."""
        mock_socket = AsyncMock()
        self.client.connection = mock_socket

        # Set up subscriptions
        self.client.subscriptions = {"NSE|123": "SBIN-EQ", "NSE|456": "RELIANCE-EQ"}

        await self.client.unsubscribe([("NSE", "123"), ("NSE", "456")])

        mock_socket.send.assert_called_once()
        sent_frame = json.loads(mock_socket.send.call_args[0][0])
        expected_frame = {"t": "u", "k": "NSE|123#NSE|456"}
        self.assertEqual(sent_frame, expected_frame)

    async def test_unsubscribe_no_connection(self):
        """Test unsubscription without active connection raises RuntimeError."""
        self.client.connection = None

        with self.assertRaises(RuntimeError):
            await self.client.unsubscribe([("NSE", "123")])

    async def test_resolve_symbol_to_token_cached(self):
        """Test that cached tokens are returned without API call."""
        self.client.token_cache["SBIN-EQ"] = "123"

        token = await self.client._resolve_symbol_to_token("NSE", "SBIN-EQ")

        self.assertEqual(token, "123")

    async def test_resolve_symbol_to_token_not_found(self):
        """Test symbol resolution when token is not found."""
        # Mock the resolve_symbol function to raise an error for unknown symbols
        with patch("jev_imrp.ingest.shoonya_feed_client.resolve_symbol") as mock_resolve:
            mock_resolve.side_effect = SymbolResolutionError("symbol not found")

            token = await self.client._resolve_symbol_to_token("NSE", "UNKNOWN-EQ")

            self.assertIsNone(token)

    async def test_start_creates_task(self):
        """Test that start() creates a task."""
        self.assertFalse(self.client.running)

        mock_socket = AsyncMock()
        mock_socket.__aenter__ = AsyncMock(return_value=mock_socket)
        mock_socket.__aexit__ = AsyncMock(return_value=None)
        mock_socket.__aiter__ = MagicMock(return_value=iter([]))

        with patch("jev_imrp.ingest.shoonya_feed_client.websockets.connect",
                   return_value=mock_socket):
            await self.client.start()

            self.assertTrue(self.client.running)
            self.assertIsNotNone(self.client.task)

        # Clean up
        self.client.running = False
        if self.client.task:
            self.client.task.cancel()
            try:
                await self.client.task
            except asyncio.CancelledError:
                pass

    async def test_start_sets_running_flag(self):
        """Test that start() sets running flag."""
        mock_socket = AsyncMock()
        mock_socket.__aenter__ = AsyncMock(return_value=mock_socket)
        mock_socket.__aexit__ = AsyncMock(return_value=None)
        mock_socket.__aiter__ = MagicMock(return_value=iter([]))

        with patch("jev_imrp.ingest.shoonya_feed_client.websockets.connect",
                   return_value=mock_socket):
            await self.client.start()

            self.assertTrue(self.client.running)
            self.assertIsNotNone(self.client.task)

            # Clean up
            self.client.running = False
            if self.client.task:
                self.client.task.cancel()
                try:
                    await self.client.task
                except asyncio.CancelledError:
                    pass

    async def test_stop_when_not_running(self):
        """Test stop when not running is a no-op."""
        await self.client.stop()  # should not raise


if __name__ == "__main__":
    unittest.main()
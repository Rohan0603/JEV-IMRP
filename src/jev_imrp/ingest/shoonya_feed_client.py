"""Shoonya WebSocket feed client for JEV-IMRP research platform.

This module implements a WebSocket client for connecting to Shoonya's real-time
market data feed and integrating with the existing Recorder infrastructure.

Key features:
- WebSocket connection to `wss://api.shoonya.com/NorenWSAPI/`
- Subscription management for multiple instruments
- Message parsing and integration with Recorder
- Symbol-to-token resolution via REST API
- Error handling and reconnection logic
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from jev_imrp.ingest.shoonya_recorder import Recorder
from jev_imrp.ingest.symbol_resolver import SymbolResolutionError, resolve_symbol

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShoonyaFeedClient:
    """WebSocket client for Shoonya market data feed."""

    def __init__(
        self,
        recorder: Recorder,
        user_id: str,
        account_id: str,
        access_token: str,
        max_reconnect_attempts: int = 5,
        reconnect_delay: float = 5.0,
    ):
        """Initialize the feed client.

        Args:
            recorder: The Recorder instance to feed messages to
            user_id: User ID for authentication
            account_id: Account ID for authentication
            access_token: Access token for authentication
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Delay between reconnection attempts (seconds)
        """
        self.recorder = recorder
        self.user_id = user_id
        self.account_id = account_id
        self.access_token = access_token
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.subscriptions: dict[str, str] = {}  # exchange|token -> symbol
        self.token_cache: dict[str, str] = {}  # symbol -> token
        self.connection: ClientConnection | None = None
        self.running = False
        self.task: asyncio.Task[Any] | None = None

    async def connect(self) -> None:
        """Establish WebSocket connection to Shoonya."""
        try:
            async with websockets.connect(
                "wss://api.shoonya.com/NorenWSAPI/",
                ping_interval=30,
                ping_timeout=60,
            ) as ws:
                self.connection = ws
                await self._handle_connection(ws)
        except Exception as e:
            logger.error(f"Connection error: {e}")
            # Re-raise to be caught by the run method for reconnection logic
            raise

    async def _handle_connection(self, ws: ClientConnection) -> None:
        """Handle WebSocket connection lifecycle."""
        try:
            # Send connect frame
            connect_frame = {
                "t": "a",
                "uid": self.user_id,
                "actid": self.account_id,
                "source": "API",
                "accesstoken": self.access_token,
            }
            await ws.send(json.dumps(connect_frame))
            logger.info("Sent connect frame")

            # Process incoming messages
            async for message in ws:
                try:
                    payload = json.loads(message)
                    await self._process_message(payload)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode message: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed")
            # Re-raise to trigger reconnection in run method
            raise
        except Exception as e:
            logger.error(f"Connection error: {e}")
            # Re-raise to trigger reconnection in run method
            raise

    async def _process_message(self, payload: dict[str, Any]) -> None:
        """Process incoming WebSocket message."""
        message_type = payload.get("t")

        if message_type == "tk" or message_type == "tf":
            # Feed to recorder for state reconstruction
            # Note: The recorder.feed() method returns the raw event and the new quote state.
            # We are primarily interested in feeding the data to the recorder here.
            self.recorder.feed(payload)
            logger.debug(f"Processed {message_type} message")
        elif message_type == "uk":
            # Unsubscribe acknowledge
            logger.debug(f"Unsubscribe acknowledged: {payload}")
        elif message_type in ["ak", "ck"]:
            # Connection acknowledge
            logger.debug(f"Connection acknowledged: {payload}")
        elif message_type == "error":
            # Error message
            logger.error(f"Feed error: {payload.get('emsg', 'Unknown error')}")
        else:
            # Unknown message type
            logger.warning(f"Unknown message type: {message_type}")

    async def subscribe(self, instruments: list[tuple[str, str]]) -> None:
        """Subscribe to instruments.

        Args:
            instruments: List of (exchange, symbol) tuples
        """
        if not self.connection:
            raise RuntimeError("Not connected to WebSocket")

        # Resolve symbols to tokens
        resolved_instruments_for_sub = []
        for exchange, symbol in instruments:
            token = await self._resolve_symbol_to_token(exchange, symbol)
            if token:
                resolved_instruments_for_sub.append((exchange, token, symbol))
            else:
                logger.warning(f"Could not resolve symbol {symbol} for subscription.")

        if not resolved_instruments_for_sub:
            logger.warning("No valid instruments to subscribe")
            return

        # Build subscription frame
        k = "#".join(f"{exchange}|{token}" for exchange, token, _ in resolved_instruments_for_sub)
        subscribe_frame = {"t": "t", "k": k}

        try:
            await self.connection.send(json.dumps(subscribe_frame))
            logger.info(f"Subscribed to {len(resolved_instruments_for_sub)} instruments")

            # Cache subscriptions for potential later use (e.g., reconnect)
            for exchange, token, symbol in resolved_instruments_for_sub:
                self.subscriptions[f"{exchange}|{token}"] = symbol
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")

    async def _resolve_symbol_to_token(self, exchange: str, symbol: str) -> str | None:
        """Resolve symbol to token using the Shoonya REST API.

        Args:
            exchange: Exchange code (e.g., "NSE")
            symbol: Instrument symbol (e.g., "SBIN-EQ")

        Returns:
            Token if found, None otherwise
        """
        # Check cache first
        if symbol in self.token_cache:
            return self.token_cache[symbol]

        try:
            # Use the implemented symbol resolver
            resolved_token = resolve_symbol(
                exchange=exchange,
                symbol=symbol,
                use_master=True,  # Prefer symbol master
                user_id=self.user_id,  # Pass user_id for fallback
            )
            token = resolved_token.token
        except SymbolResolutionError as e:
            logger.error(f"Failed to resolve symbol {symbol} on {exchange}: {e}")
            token = None

        # Cache the result
        if token:
            self.token_cache[symbol] = token
        return token

    async def unsubscribe(self, instruments: list[tuple[str, str]]) -> None:
        """Unsubscribe from instruments.

        Args:
            instruments: List of (exchange, token) tuples
        """
        if not self.connection:
            raise RuntimeError("Not connected to WebSocket")

        # Build unsubscription frame
        k = "#".join(f"{exchange}|{token}" for exchange, token in instruments)
        unsubscribe_frame = {"t": "u", "k": k}

        try:
            await self.connection.send(json.dumps(unsubscribe_frame))
            logger.info(f"Unsubscribed from {len(instruments)} instruments")

            # Remove from subscriptions
            for exchange, token in instruments:
                key = f"{exchange}|{token}"
                if key in self.subscriptions:
                    del self.subscriptions[key]
        except Exception as e:
            logger.error(f"Failed to unsubscribe: {e}")

    async def start(self) -> None:
        """Start the feed client."""
        if self.running:
            return

        self.running = True
        # Create a task for the connection/handling loop
        self.task = asyncio.create_task(self.connect())

    async def stop(self) -> None:
        """Stop the feed client."""
        if not self.running or not self.task:
            return

        self.running = False
        try:
            # Cancel the task if it's running
            if not self.task.done():
                self.task.cancel()
                await self.task  # Wait for cancellation to complete
        except asyncio.CancelledError:
            pass  # Expected when cancelling
        finally:
            self.task = None
            self.connection = None  # Clear connection on stop

    async def run(self) -> None:
        """Run the feed client with reconnection logic."""
        attempt = 0
        while self.running and attempt < self.max_reconnect_attempts:
            try:
                await self.start()  # This will create the task
                task = self.task
                if task is not None:
                    await task  # Wait for the connection task to complete (or fail)
                # If task completes without error, it means connection was closed cleanly or by us
                if (
                    self.running
                ):  # If we are still supposed to be running, it means an unexpected close
                    logger.warning("Connection task finished unexpectedly. Attempting reconnect.")
                    attempt += 1
                    await asyncio.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                attempt += 1
                if self.running and attempt < self.max_reconnect_attempts:
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    logger.error("Max reconnection attempts reached or client stopped.")
                    self.running = False  # Ensure running is false if max attempts reached
                    break

        # Ensure resources are cleaned up if loop exits
        if not self.running:
            await self.stop()


__all__ = ["ShoonyaFeedClient"]

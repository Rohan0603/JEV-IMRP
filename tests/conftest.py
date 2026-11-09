from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def deny_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every deterministic test from contacting a broker or the internet."""

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def is_loopback(address: object) -> bool:
        if not isinstance(address, tuple) or not address:
            return True
        return address[0] in {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(sock: socket.socket, address: object) -> None:
        if is_loopback(address):
            original_connect(sock, address)  # type: ignore[arg-type]
            return
        raise RuntimeError("outbound network is disabled in the test suite")

    def guarded_connect_ex(sock: socket.socket, address: object) -> int:
        if is_loopback(address):
            return original_connect_ex(sock, address)  # type: ignore[arg-type]
        raise RuntimeError("outbound network is disabled in the test suite")

    def blocked_create_connection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("outbound network is disabled in the test suite")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", blocked_create_connection)

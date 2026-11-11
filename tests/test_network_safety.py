from __future__ import annotations

import socket

import pytest


def test_test_suite_denies_outbound_socket_creation() -> None:
    with pytest.raises(RuntimeError, match="outbound network is disabled"):
        socket.create_connection(("127.0.0.1", 9), timeout=0.01)

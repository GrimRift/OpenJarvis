"""Outbound connections try IPv4 first (core/net.py, 23 September)."""

from __future__ import annotations

import socket

from openjarvis.core import net


def test_ipv4_comes_first_and_ipv6_is_kept(monkeypatch) -> None:
    v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::f3", 443, 0, 0))
    v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("162.159.140.245", 443))
    monkeypatch.setattr(net, "_original_getaddrinfo", lambda *a, **k: [v6, v4])
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    net.prefer_ipv4()
    net.prefer_ipv4()
    assert socket.getaddrinfo("api.openai.com", 443) == [v4, v6]

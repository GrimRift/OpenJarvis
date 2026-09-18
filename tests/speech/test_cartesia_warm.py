"""A spoken reply should not pay for a TLS handshake.

Measured 18 September: opening the Cartesia websocket cost about a second of
the silence before Sage's voice began -- the handshake, not the distance
(their edge answers in 9 ms). During a voice conversation a socket is opened
ahead of the turn that needs it.
"""

from __future__ import annotations

import asyncio

import pytest

from openjarvis.speech import cartesia_tts as ct


class _Socket:
    def __init__(self, dead: bool = False) -> None:
        self.close_code = 1000 if dead else None
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.close_code = 1000


@pytest.fixture(autouse=True)
def _no_parked_socket():
    ct._warm_socket = None
    ct._warm_opened_at = 0.0
    yield
    ct._warm_socket = None


def test_a_parked_socket_is_handed_to_the_next_turn(monkeypatch):
    opened = []

    async def _connect(key):
        opened.append(key)
        return _Socket()

    monkeypatch.setattr(ct, "_connect", _connect)
    assert asyncio.run(ct.warm_connection("k")) is True
    assert len(opened) == 1

    taken = ct._take_warm()
    assert isinstance(taken, _Socket)
    # Handed over, not shared: a second turn must open its own.
    assert ct._take_warm() is None


def test_warming_twice_keeps_the_first_socket(monkeypatch):
    opened = []

    async def _connect(key):
        opened.append(key)
        return _Socket()

    monkeypatch.setattr(ct, "_connect", _connect)

    async def twice():
        await ct.warm_connection("k")
        await ct.warm_connection("k")

    asyncio.run(twice())
    assert len(opened) == 1


def test_a_stale_or_dead_socket_is_never_used(monkeypatch):
    monkeypatch.setattr(ct, "_connect", lambda key: _async(_Socket()))

    async def _async(value):
        return value

    # Closed by Cartesia while parked.
    ct._warm_socket = _Socket(dead=True)
    ct._warm_opened_at = ct.time.monotonic()
    assert ct._take_warm() is None

    # Open, but parked longer than the TTL.
    ct._warm_socket = _Socket()
    ct._warm_opened_at = ct.time.monotonic() - ct.WARM_TTL_SECONDS - 1
    assert ct._take_warm() is None


def test_warming_never_raises(monkeypatch):
    async def _boom(key):
        raise OSError("no route to host")

    monkeypatch.setattr(ct, "_connect", _boom)
    # A failed warm-up must cost nothing: the turn connects on demand.
    assert asyncio.run(ct.warm_connection("k")) is False
    assert ct._take_warm() is None


def test_dropping_closes_what_is_parked(monkeypatch):
    socket = _Socket()
    ct._warm_socket = socket
    ct._warm_opened_at = ct.time.monotonic()
    asyncio.run(ct.drop_warm_connection())
    assert socket.closed is True
    assert ct._take_warm() is None

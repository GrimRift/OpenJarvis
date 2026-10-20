"""Pairing a phone with a one-time code (M29, server/pairing.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server import pairing
from openjarvis.server.auth_middleware import AuthMiddleware
from openjarvis.server.pairing import (
    CODE_TTL_S,
    MAX_WRONG,
    PairingCodes,
    tailnet_address,
)
from openjarvis.server.pairing_routes import create_pairing_router

KEY = "oj_sk_test-key"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_codes(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
    monkeypatch.setattr(pairing, "codes", PairingCodes())
    monkeypatch.setattr(pairing, "tailnet_address", lambda: "https://pc.tail1.ts.net/")


def _client(api_key: str = KEY) -> TestClient:
    app = FastAPI()
    app.state.api_key = api_key
    app.add_middleware(AuthMiddleware, api_key=api_key)
    app.include_router(create_pairing_router())
    return TestClient(app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {KEY}"}


class TestCodes:
    def test_a_code_is_six_digits_and_spent_by_one_redeem(self):
        codes = PairingCodes()
        code, ttl = codes.issue()
        assert len(code) == 6 and code.isdigit() and ttl == CODE_TTL_S
        assert codes.redeem(code) == "ok"
        assert codes.redeem(code) == "none"

    def test_spaces_or_dashes_typed_on_the_phone_are_ignored(self):
        codes = PairingCodes()
        code, _ = codes.issue()
        assert codes.redeem(f"{code[:3]} {code[3:]}") == "ok"

    def test_it_expires(self):
        clock = Clock()
        codes = PairingCodes(clock)
        code, _ = codes.issue()
        clock.now += CODE_TTL_S
        assert codes.redeem(code) == "none"

    def test_wrong_guesses_kill_it(self):
        codes = PairingCodes()
        code, _ = codes.issue()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_WRONG):
            assert codes.redeem(wrong) == "wrong"
        # Dead: even the right code no longer works.
        assert codes.redeem(code) == "none"

    def test_a_new_code_replaces_the_old_one(self):
        codes = PairingCodes()
        first, _ = codes.issue()
        second, _ = codes.issue()
        if first != second:
            assert codes.redeem(first) == "wrong"
        assert codes.redeem(second) == "ok"

    def test_nothing_to_redeem_before_a_code_is_made(self):
        assert PairingCodes().redeem("123456") == "none"


class TestRoutes:
    def test_making_a_code_needs_the_key(self):
        client = _client()
        assert client.post("/v1/pairing/code").status_code == 401
        res = client.post("/v1/pairing/code", headers=_auth())
        assert res.status_code == 200
        body = res.json()
        assert len(body["code"]) == 6
        assert body["address"] == "https://pc.tail1.ts.net/"

    def test_redeeming_needs_no_key_and_returns_it(self):
        client = _client()
        code = client.post("/v1/pairing/code", headers=_auth()).json()["code"]
        res = client.post("/v1/pairing/redeem", json={"code": code})
        assert res.status_code == 200
        assert res.json() == {"api_key": KEY, "settings": {}}
        # Spent.
        assert client.post("/v1/pairing/redeem", json={"code": code}).status_code == 410

    def test_a_wrong_code_gets_nothing(self):
        client = _client()
        code = client.post("/v1/pairing/code", headers=_auth()).json()["code"]
        wrong = "000000" if code != "000000" else "111111"
        res = client.post("/v1/pairing/redeem", json={"code": wrong})
        assert res.status_code == 403
        assert KEY not in res.text

    def test_only_redeem_is_open_without_the_key(self):
        client = _client()
        assert client.delete("/v1/pairing/code").status_code == 401


class TestAddress:
    def test_reads_the_tailnet_name(self):
        out = '{"Self": {"DNSName": "grim.tailf6ae6f.ts.net."}}'
        assert tailnet_address(lambda: out) == "https://grim.tailf6ae6f.ts.net/"

    def test_empty_without_tailscale(self):
        def boom() -> str:
            raise FileNotFoundError("tailscale")

        assert tailnet_address(boom) == ""
        assert tailnet_address(lambda: "") == ""
        assert tailnet_address(lambda: '{"Self": {"DNSName": "grim.local."}}') == ""


class TestSettings:
    def test_the_pcs_settings_ride_the_code_to_the_phone(self):
        client = _client()
        sent = {"speechEnabled": True, "ttsVoiceId": "frieren"}
        code = client.post(
            "/v1/pairing/code", headers=_auth(), json={"settings": sent}
        ).json()["code"]
        res = client.post("/v1/pairing/redeem", json={"code": code})
        assert res.json()["settings"] == sent

    def test_a_wrong_guess_gets_no_settings(self):
        codes = PairingCodes()
        code, _ = codes.issue({"speechEnabled": True})
        wrong = "000000" if code != "000000" else "111111"
        assert codes.redeem_with_settings(wrong) == ("wrong", None)

    def test_an_oversized_body_is_ignored_not_stored(self):
        client = _client()
        huge = {"settings": {"x": "y" * 70_000}}
        made = client.post("/v1/pairing/code", headers=_auth(), json=huge)
        code = made.json()["code"]
        res = client.post("/v1/pairing/redeem", json={"code": code})
        assert res.json()["settings"] == {}

"""Credentials must not survive a failure message.

An API key reached a tool result, the model's context and the logs through
an httpx error quoting the request URL it failed on. The stripper existed at
the time and did not catch it: its rules matched credentials by *shape*
(sk-, AKIA, ghp_, xoxb-, Bearer) and an OpenWeatherMap key is thirty-two hex
characters with no prefix at all.

These tests hold the position-based rule that replaced it -- the value of a
credential-named parameter is secret whatever it looks like.
"""

from __future__ import annotations

import httpx
import pytest

from openjarvis.core.types import ToolCall
from openjarvis.security.credential_stripper import CredentialStripper
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

SECRET = "e68e8c7f2fee6ee94f8bdc067f6c6898"


@pytest.fixture()
def stripper():
    return CredentialStripper()


class TestCredentialsWithNoRecognisableShape:
    """The class of key the shape-based rules were blind to."""

    @pytest.mark.parametrize(
        "url",
        [
            f"https://api.openweathermap.org/data/2.5/weather?lat=14.1&appid={SECRET}",
            f"https://api.tavily.com/search?api_key={SECRET}&q=x",
            f"https://example.com/v1?access_token={SECRET}",
            f"https://example.com/v1?client_secret={SECRET}",
            f"https://example.com/v1?password={SECRET}",
            f"https://example.com/v1?SIGNATURE={SECRET}",
        ],
    )
    def test_the_value_is_removed(self, stripper, url):
        out = stripper.strip(f"401 Unauthorized for url '{url}'")
        assert SECRET not in out
        assert "REDACTED" in out

    def test_the_rest_of_the_url_survives(self, stripper):
        """A redacted message still has to be diagnosable."""
        out = stripper.strip(
            f"401 for https://api.openweathermap.org/data/2.5/weather?lat=14.1&appid={SECRET}&units=metric"
        )
        assert "openweathermap.org" in out
        assert "lat=14.1" in out
        assert "units=metric" in out

    def test_an_innocent_url_is_left_alone(self, stripper):
        text = "Timeout for https://example.com/search?q=weather&units=metric"
        assert stripper.strip(text) == text


class TestTelegramPutsItsTokenInThePath:
    """No query rule can catch a secret that is not a parameter."""

    def test_the_bot_token_is_removed(self, stripper):
        out = stripper.strip(
            "ConnectError for https://api.telegram.org/bot8509134310:AAH7xQ2vExampleTokenValue/sendMessage"
        )
        assert "AAH7xQ2vExampleTokenValue" not in out
        assert "api.telegram.org" in out


class TestTheGenericToolErrorPath:
    """Every tool's unexpected failure passes through one place."""

    class _Leaky(BaseTool):
        tool_id = "leaky"

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name="leaky",
                description="raises the way an HTTP client does",
                parameters={"type": "object", "properties": {}},
            )

        def execute(self, **params):
            request = httpx.Request(
                "GET",
                "https://api.example.com/v1/thing",
                params={"appid": SECRET},
            )
            raise httpx.HTTPStatusError(
                f"401 Unauthorized for url '{request.url}'",
                request=request,
                response=httpx.Response(401, request=request),
            )

    def test_a_leaked_key_does_not_reach_the_result(self):
        executor = ToolExecutor([self._Leaky()])
        result = executor.execute(
            ToolCall(id="1", name="leaky", arguments="{}")
        )
        assert result.success is False
        assert SECRET not in result.content

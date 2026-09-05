"""A security profile must not silently discard what the user set.

Setting `host = "0.0.0.0"` under [server] did nothing and said nothing: the
profile's server keys were checked against a set built from the [security]
keys alone, so `host` could never appear in it and every profile replaced
the bind address with 127.0.0.1. Sage was restarted twice before netstat
showed it was still on loopback.
"""

from __future__ import annotations

import logging

from openjarvis.core.config import (
    SecurityConfig,
    ServerConfig,
    apply_security_profile,
)


def _personal() -> SecurityConfig:
    return SecurityConfig(profile="personal")


class TestAnExplicitServerSettingWins:
    def test_the_user_s_host_survives_the_profile(self):
        server = ServerConfig(host="0.0.0.0")
        apply_security_profile(
            _personal(), server, server_overrides={"host"}
        )
        assert server.host == "0.0.0.0"

    def test_the_profile_still_applies_when_the_user_said_nothing(self):
        """The floor is the point of a profile; it holds by default."""
        server = ServerConfig(host="0.0.0.0")
        apply_security_profile(_personal(), server)
        assert server.host == "127.0.0.1"

    def test_security_keys_are_unaffected_by_the_server_exemption(self):
        security = _personal()
        apply_security_profile(
            security, ServerConfig(), server_overrides={"host"}
        )
        assert security.mode == "redact"
        assert security.rate_limit_enabled is True
        assert security.local_tool_bypass is False


class TestItSaysWhenItOverrides:
    def test_an_override_is_logged(self, caplog):
        """Silence is how a setting gets set three times and never takes."""
        with caplog.at_level(logging.INFO, logger="openjarvis.core.config"):
            apply_security_profile(_personal(), ServerConfig(host="0.0.0.0"))
        assert any("127.0.0.1" in r.getMessage() for r in caplog.records)

    def test_no_log_when_nothing_changes(self, caplog):
        with caplog.at_level(logging.INFO, logger="openjarvis.core.config"):
            apply_security_profile(_personal(), ServerConfig(host="127.0.0.1"))
        assert not caplog.records

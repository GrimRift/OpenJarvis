"""The busy model behind initiative (M37): when not to start a conversation."""

from __future__ import annotations

from openjarvis.core import activity
from openjarvis.core.activity import Activity
from openjarvis.core.busy import busy_reasons, is_fullscreen


def _reasons(act=None, *, window=None, monitor=(1920, 1080), mic=()):
    return busy_reasons(
        act or Activity(),
        foreground=lambda: window,
        monitor=lambda w: monitor,
        mic_apps=lambda: list(mic),
    )


class TestFullscreen:
    def test_a_window_the_size_of_its_monitor_is_full_screen(self) -> None:
        assert is_fullscreen({"width": 1920, "height": 1080}, (1920, 1080))

    def test_a_maximised_window_is_not(self) -> None:
        # Its frame sits outside the work area, so it is larger, not equal.
        assert not is_fullscreen({"width": 1936, "height": 1096}, (1920, 1080))

    def test_nothing_in_front_is_not(self) -> None:
        assert not is_fullscreen(None, (1920, 1080))


class TestBusyReasons:
    def test_a_quiet_desk_is_not_busy(self) -> None:
        assert (
            _reasons(window={"title": "Sage - Opera", "width": 1936, "height": 1096})
            == []
        )

    def test_sage_mid_turn(self) -> None:
        assert _reasons(Activity(tts_streams=1)) == ["Sage is mid-turn"]
        assert _reasons(Activity(flux_transmitting=True)) == ["Sage is mid-turn"]

    def test_a_film_in_front(self) -> None:
        reasons = _reasons(window={"title": "Netflix", "width": 1920, "height": 1080})
        assert reasons == ["full-screen: Netflix"]

    def test_a_call_in_the_browser(self) -> None:
        reasons = _reasons(
            window={"title": "Meet - abc-defg - Opera", "width": 10, "height": 10}
        )
        assert reasons and reasons[0].startswith("a call in front")

    def test_teams_with_the_microphone_only(self) -> None:
        assert _reasons(mic=["MSTeams_8wekyb3d8bbwe"]) == [
            "MSTeams_8wekyb3d8bbwe has the microphone"
        ]
        # Sage's own browser tab holds the microphone for the wake word.
        assert _reasons(mic=["C:#Users#x#opera.exe"]) == []


class TestActivity:
    def test_the_routes_record_what_the_server_sees(self) -> None:
        activity.reset()
        activity.note_user_turn(100.0)
        activity.tts_begin()
        snap = activity.snapshot()
        assert snap.last_user_turn_at == 100.0 and snap.sage_mid_turn
        activity.tts_end(130.0)
        snap = activity.snapshot()
        assert not snap.sage_mid_turn and snap.last_reply_end_at == 130.0
        activity.flux_transmitting(True)
        assert activity.snapshot().sage_mid_turn
        activity.flux_transmitting(False)
        assert not activity.snapshot().sage_mid_turn
        activity.reset()

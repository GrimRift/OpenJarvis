"""Sage starts Opera when it is closed, and a read hands the front back to the app."""

from unittest.mock import patch

from openjarvis.tools import opera_control, reading_focus

# Taken before the shared fixture (conftest.py) stubs them out for every test.
_REAL_LAUNCH = opera_control._launch_opera


class TestEnsureOpera:
    def test_a_running_controllable_opera_is_used_as_is(self, monkeypatch):
        monkeypatch.setattr(opera_control, "port_is_open", lambda timeout=1.5: True)
        launched = []
        monkeypatch.setattr(
            opera_control, "_launch_opera", lambda m: launched.append(m)
        )
        assert opera_control.ensure_opera() is None
        assert launched == []

    def test_opera_open_without_the_port_is_explained_not_restarted(self, monkeypatch):
        monkeypatch.setattr(opera_control, "port_is_open", lambda timeout=1.5: False)
        monkeypatch.setattr(opera_control, "_opera_running", lambda: True)
        launched = []
        monkeypatch.setattr(
            opera_control, "_launch_opera", lambda m: launched.append(m)
        )
        problem = opera_control.ensure_opera()
        assert "Close Opera completely" in problem
        assert launched == []

    def test_a_closed_opera_is_started_and_waited_for(self, monkeypatch):
        answers = iter([False, False, True])
        monkeypatch.setattr(
            opera_control, "port_is_open", lambda timeout=1.5: next(answers)
        )
        launched = []
        monkeypatch.setattr(
            opera_control, "_launch_opera", lambda m: launched.append(m) or None
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        assert opera_control.ensure_opera(minimized=True) is None
        assert launched == [True]

    def test_a_launch_that_cannot_happen_says_why(self, monkeypatch):
        monkeypatch.setattr(opera_control, "port_is_open", lambda timeout=1.5: False)
        monkeypatch.setattr(opera_control, "_launch_opera", lambda m: "no Opera here")
        assert opera_control.ensure_opera() == "no Opera here"

    def test_a_read_starts_opera_minimised_with_the_port(self, monkeypatch):
        seen = {}

        def fake_popen(args, startupinfo=None, **kwargs):
            seen["args"] = args
            seen["show"] = getattr(startupinfo, "wShowWindow", None)

        monkeypatch.setattr(opera_control.os.path, "isfile", lambda p: True)
        with patch("subprocess.Popen", fake_popen):
            assert _REAL_LAUNCH(minimized=True) is None
        assert "--remote-debugging-port=9222" in seen["args"]
        assert seen["show"] in (None, 7)  # 7 = minimised without focus (Windows)

    def test_a_missing_opera_gives_the_setup_steps(self, monkeypatch):
        monkeypatch.setattr(opera_control.os.path, "isfile", lambda p: False)
        assert "--remote-debugging-port" in _REAL_LAUNCH(minimized=False)


class TestReadingFocus:
    def test_opera_minimised_before_is_minimised_again_and_sage_called(
        self, monkeypatch
    ):
        states = iter([{11: True, 22: False}, {11: False, 22: False}])
        monkeypatch.setattr(reading_focus, "_opera_windows", lambda: next(states))
        minimised, returned = [], []
        monkeypatch.setattr(reading_focus.sys, "platform", "win32")

        class User32:
            def ShowWindow(self, handle, command):
                minimised.append((handle, command))

        class Windll:
            user32 = User32()

        monkeypatch.setattr("ctypes.windll", Windll(), raising=False)
        focus = reading_focus.ReadingFocus()
        monkeypatch.setattr(
            reading_focus.ReadingFocus,
            "_return_to_sage",
            lambda self: returned.append(1),
        )
        # The shared fixture no-ops finish(); run the real steps directly.
        focus._put_opera_back()
        focus._return_to_sage()
        assert minimised == [(11, 7)]  # only the one that was minimised before
        assert returned == [1]

    def test_no_app_running_means_nothing_is_launched(self, monkeypatch):
        monkeypatch.setattr(reading_focus.sys, "platform", "win32")
        monkeypatch.setattr(reading_focus, "_running_app_exe", lambda: "")
        with patch("subprocess.Popen") as popen:
            reading_focus.ReadingFocus.__new__(reading_focus.ReadingFocus)._return_to_sage()
        assert not popen.called

    def test_a_running_app_is_asked_to_come_back(self, monkeypatch, tmp_path):
        exe = tmp_path / "sage-desktop.exe"
        exe.write_text("")
        monkeypatch.setattr(reading_focus.sys, "platform", "win32")
        monkeypatch.setattr(reading_focus, "_running_app_exe", lambda: str(exe))
        with patch("subprocess.Popen") as popen:
            reading_focus.ReadingFocus.__new__(reading_focus.ReadingFocus)._return_to_sage()
        assert popen.call_args[0][0] == [str(exe), "--return-focus"]


class TestWarmUp:
    def test_waits_are_longer_just_after_sage_started_opera(self, monkeypatch):
        import time

        monkeypatch.setattr(opera_control, "_LAUNCHED_AT", 0.0)
        assert opera_control.load_timeout(25.0) == 25.0
        monkeypatch.setattr(opera_control, "_LAUNCHED_AT", time.monotonic())
        assert opera_control.load_timeout(25.0) == 75.0
        monkeypatch.setattr(opera_control, "_LAUNCHED_AT", time.monotonic() - 600)
        assert opera_control.load_timeout(25.0) == 25.0

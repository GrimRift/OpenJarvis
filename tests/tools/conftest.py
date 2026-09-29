"""Tool tests never start the user's real Opera GX or move real windows."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_opera(monkeypatch):
    from openjarvis.tools import opera_control, reading_focus

    monkeypatch.setattr(opera_control, "_opera_running", lambda: False)
    monkeypatch.setattr(
        opera_control, "_launch_opera", lambda minimized: opera_control.setup_hint()
    )
    monkeypatch.setattr(reading_focus.ReadingFocus, "finish", lambda self: None)

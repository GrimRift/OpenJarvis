"""Acting on the desktop: the guards, mostly.

This is the first thing in Sage that can change something the user did not
watch happen, so these tests are weighted toward what it refuses rather than
what it does. The happy paths are short; the refusals are the point.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.security import confirmations
from openjarvis.tools.desktop_control import (
    ClickAtTool,
    ClickControlTool,
    TypeTextTool,
)
from openjarvis.tools.desktop_monitors import Monitor

MAIN = Monitor(
    index=1,
    device=r"\\.\DISPLAY2",
    name="",
    width=1920,
    height=1080,
    x=0,
    y=0,
    is_primary=True,
)


@pytest.fixture(autouse=True)
def _clean_confirmations():
    confirmations.clear()
    confirmations.set_turn([{"role": "user", "content": "do the thing"}])
    yield
    confirmations.clear()


def _control(name: str, patterns=("Invoke",)):
    control = MagicMock()
    control.Name = name
    control.GetChildren.return_value = []
    for label, getter in (
        ("Invoke", "GetInvokePattern"),
        ("Toggle", "GetTogglePattern"),
        ("Expand", "GetExpandCollapsePattern"),
        ("Value", "GetValuePattern"),
    ):
        pattern = MagicMock() if label in patterns else None
        setattr(control, getter, MagicMock(return_value=pattern))
    return control


def _window(title: str, children):
    window = MagicMock()
    window.Name = title
    window.GetChildren.return_value = children
    return window


def _uia_with(window):
    auto = MagicMock()
    root = MagicMock()
    root.GetChildren.return_value = [window]
    auto.GetRootControl.return_value = root
    return auto


def _patch_uia(window):
    return patch(
        "openjarvis.tools.desktop_control._uia",
        return_value=_uia_with(window),
    )


def _approve(tool_id, params):
    """A real second turn saying yes, as the confirmation path requires."""
    confirmations.decide(tool_id, params)
    confirmations.set_turn(
        [
            {"role": "user", "content": "do the thing"},
            {"role": "assistant", "content": "Confirm?"},
            {"role": "user", "content": "yes"},
        ]
    )


class TestPressingANamedControl:
    def test_it_invokes_the_control(self):
        button = _control("Add New Tab")
        window = _window("Notepad", [button])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="Notepad", control="Add New Tab")

        assert result.success is True
        button.GetInvokePattern().Invoke.assert_called_once()

    def test_a_toggle_is_toggled_not_invoked(self):
        toggle = _control("Bold (Ctrl+B)", patterns=("Toggle",))
        window = _window("Notepad", [toggle])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="Notepad", control="Bold")

        assert result.metadata["action"] == "toggled"
        toggle.GetTogglePattern().Toggle.assert_called_once()

    def test_a_missing_control_names_what_to_do_next(self):
        window = _window("Notepad", [_control("Save")])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="Notepad", control="Nonexistent")
        assert result.success is False
        assert "inspect_window" in result.content

    def test_a_missing_window_names_what_to_do_next(self):
        window = _window("Notepad", [])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="Photoshop", control="Save")
        assert result.success is False
        assert "list_windows" in result.content


class TestDestructiveControlsAreConfirmed:
    def test_it_refuses_until_the_user_says_yes(self):
        button = _control("Delete Account")
        window = _window("Settings", [button])
        with _patch_uia(window):
            result = ClickControlTool().execute(
                window="Settings", control="Delete Account"
            )

        assert result.success is False
        assert result.metadata["requires_confirmation"] is True
        button.GetInvokePattern().Invoke.assert_not_called()

    def test_it_presses_once_the_user_has_agreed(self):
        button = _control("Delete Account")
        window = _window("Settings", [button])
        params = {"window": "Settings", "control": "Delete Account"}
        with _patch_uia(window):
            ClickControlTool().execute(**params)
            _approve("click_control", params)
            result = ClickControlTool().execute(**params)

        assert result.success is True
        button.GetInvokePattern().Invoke.assert_called_once()

    def test_an_ordinary_control_needs_no_confirmation(self):
        button = _control("Save")
        window = _window("Notepad", [button])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="Notepad", control="Save")
        assert result.success is True

    def test_the_confirmation_names_the_control(self):
        window = _window("Settings", [_control("Delete Account")])
        with _patch_uia(window):
            result = ClickControlTool().execute(
                window="Settings", control="Delete Account"
            )
        assert "Delete Account" in result.content


class TestSensitiveWindowsAreRefused:
    def test_it_will_not_click_in_a_password_manager(self):
        button = _control("Reveal")
        window = _window("1Password", [button])
        with _patch_uia(window):
            result = ClickControlTool().execute(window="1Password", control="Reveal")

        assert result.success is False
        assert result.metadata["redacted"] is True
        button.GetInvokePattern().Invoke.assert_not_called()

    def test_it_will_not_type_into_one(self):
        window = _window("Online Banking - Transfer", [])
        with _patch_uia(window):
            result = TypeTextTool().execute(window="Online Banking", text="hello")
        assert result.success is False
        assert result.metadata["redacted"] is True


class TestTyping:
    def test_a_named_field_gets_its_value_set(self):
        field = _control("Search box", patterns=("Value",))
        window = _window("Explorer", [field])
        with _patch_uia(window):
            result = TypeTextTool().execute(
                window="Explorer", text="report.pdf", control="Search box"
            )

        assert result.metadata["action"] == "set"
        field.GetValuePattern().SetValue.assert_called_once_with("report.pdf")

    def test_destructive_text_is_confirmed_not_the_control(self):
        """Typing is judged on what is typed — the text is the consequence."""
        field = _control("Message", patterns=("Value",))
        window = _window("Mail", [field])
        with _patch_uia(window):
            result = TypeTextTool().execute(
                window="Mail", text="delete everything", control="Message"
            )
        assert result.success is False
        assert result.metadata["requires_confirmation"] is True

    def test_ordinary_text_goes_straight_through(self):
        field = _control("Message", patterns=("Value",))
        window = _window("Mail", [field])
        with _patch_uia(window):
            result = TypeTextTool().execute(
                window="Mail", text="see you at four", control="Message"
            )
        assert result.success is True


class TestBlindClicks:
    """The one action where Sage cannot name what it is pressing."""

    def _monitors(self):
        return patch(
            "openjarvis.tools.desktop_monitors.list_monitors", return_value=[MAIN]
        )

    def test_a_coordinate_click_is_always_confirmed(self):
        with self._monitors():
            result = ClickAtTool().execute(monitor=1, x=100, y=200)
        assert result.success is False
        assert result.metadata["requires_confirmation"] is True

    def test_it_clicks_once_agreed(self):
        params = {"monitor": 1, "x": 100, "y": 200}
        auto = MagicMock()
        with self._monitors(), patch(
            "openjarvis.tools.desktop_control._uia", return_value=auto
        ):
            ClickAtTool().execute(**params)
            _approve("click_at", params)
            result = ClickAtTool().execute(**params)

        assert result.success is True
        auto.Click.assert_called_once()

    def test_a_point_outside_the_monitor_is_refused(self):
        with self._monitors():
            result = ClickAtTool().execute(monitor=1, x=9999, y=200)
        assert result.success is False
        assert "outside" in result.content

    def test_an_unknown_monitor_lists_the_real_ones(self):
        with self._monitors():
            result = ClickAtTool().execute(monitor=7, x=10, y=10)
        assert result.success is False
        assert "Available" in result.content

    def test_non_numeric_coordinates_are_refused(self):
        with self._monitors():
            result = ClickAtTool().execute(monitor=1, x="left", y=200)
        assert result.success is False


class TestAwarenessStaysReadOnly:
    """The boundary is a file, not a habit."""

    def test_the_awareness_module_still_has_no_action_calls(self):
        import ast
        import inspect

        from openjarvis.tools import desktop_awareness

        tree = ast.parse(inspect.getsource(desktop_awareness))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("Invoke", "Toggle", "SetValue", "Click", "SendKeys"):
            assert forbidden not in called


class TestKeyboardShortcuts:
    """Electron apps expose almost no accessibility tree.

    Live: asked to open a new tab in Obsidian, `click_control` found no "New
    Tab" control and correctly refused rather than guessing. The shortcut works
    perfectly well — there was simply no way to send one.
    """

    def test_a_shortcut_is_sent_to_the_window(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window) as patched:
            result = PressKeysTool().execute(window="Obsidian", keys="ctrl+t")

        assert result.success is True
        assert result.metadata["sendkeys"] == "{Ctrl}t"
        patched.return_value.SendKeys.assert_called_once()

    def test_the_window_is_focused_first(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window):
            PressKeysTool().execute(window="Obsidian", keys="ctrl+t")
        window.SetFocus.assert_called_once()

    def test_an_unreadable_chord_is_refused_not_guessed(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window) as patched:
            result = PressKeysTool().execute(window="Obsidian", keys="nonsense+x")

        assert result.success is False
        patched.return_value.SendKeys.assert_not_called()

    def test_a_closing_chord_is_confirmed(self):
        """A chord has no label, so the word list cannot read it."""
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window) as patched:
            result = PressKeysTool().execute(window="Obsidian", keys="ctrl+w")

        assert result.success is False
        assert result.metadata["requires_confirmation"] is True
        patched.return_value.SendKeys.assert_not_called()

    def test_alt_f4_is_confirmed(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window):
            result = PressKeysTool().execute(window="Obsidian", keys="alt+f4")
        assert result.success is False

    def test_an_ordinary_chord_goes_straight_through(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("Obsidian", [])
        with _patch_uia(window):
            result = PressKeysTool().execute(window="Obsidian", keys="ctrl+s")
        assert result.success is True

    def test_it_will_not_send_keys_to_a_sensitive_window(self):
        from openjarvis.tools.desktop_control import PressKeysTool

        window = _window("1Password", [])
        with _patch_uia(window) as patched:
            result = PressKeysTool().execute(window="1Password", keys="ctrl+c")

        assert result.success is False
        assert result.metadata["redacted"] is True
        patched.return_value.SendKeys.assert_not_called()


class TestShortcutParsing:
    @pytest.mark.parametrize(
        "combo,expected",
        [
            ("ctrl+t", "{Ctrl}t"),
            ("ctrl+shift+p", "{Ctrl}{Shift}p"),
            ("alt+f4", "{Alt}{F4}"),
            ("enter", "{Enter}"),
            ("esc", "{Esc}"),
            ("a", "a"),
        ],
    )
    def test_readable_chords(self, combo, expected):
        from openjarvis.tools.desktop_control import to_sendkeys

        assert to_sendkeys(combo) == expected

    @pytest.mark.parametrize("combo", ["ctrl+", "nonsense+x", "", "ctrl+shift+"])
    def test_unreadable_chords_return_nothing(self, combo):
        """Half-understanding a chord means pressing something nobody named."""
        from openjarvis.tools.desktop_control import to_sendkeys

        assert to_sendkeys(combo) is None

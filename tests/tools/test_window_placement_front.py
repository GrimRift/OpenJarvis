"""bring_to_front gets past Windows' focus guard without synthesising input."""

from openjarvis.tools import window_placement


class _User32:
    def __init__(self, first_try_works):
        self.first_try_works = first_try_works
        self.front = 100
        self.calls = []

    def SetForegroundWindow(self, handle):
        self.calls.append(("SetForegroundWindow", handle))
        if self.first_try_works or ("AttachThreadInput", True) in self.calls:
            self.front = handle
            return 1
        return 0

    def GetForegroundWindow(self):
        return self.front

    def GetWindowThreadProcessId(self, handle, _pid):
        return 7

    def AttachThreadInput(self, own, other, attach):
        self.calls.append(("AttachThreadInput", attach))
        return 1

    def BringWindowToTop(self, handle):
        self.calls.append(("BringWindowToTop", handle))
        return 1


class _Kernel32:
    def GetCurrentThreadId(self):
        return 3


def _patch(monkeypatch, user32):
    class Windll:
        pass

    windll = Windll()
    windll.user32 = user32
    windll.kernel32 = _Kernel32()
    monkeypatch.setattr(window_placement.ctypes, "windll", windll, raising=False)


def test_a_plain_request_that_works_needs_nothing_more(monkeypatch):
    user32 = _User32(first_try_works=True)
    _patch(monkeypatch, user32)
    assert window_placement.bring_to_front(42) is True
    assert all(call[0] != "AttachThreadInput" for call in user32.calls)


def test_a_refused_request_borrows_the_front_windows_input_then_lets_go(monkeypatch):
    user32 = _User32(first_try_works=False)
    _patch(monkeypatch, user32)
    assert window_placement.bring_to_front(42) is True
    attaches = [call[1] for call in user32.calls if call[0] == "AttachThreadInput"]
    assert attaches == [True, False]

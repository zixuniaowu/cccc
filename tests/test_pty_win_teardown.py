from __future__ import annotations

from cccc.runners.pty_win import PtySession


class _FakeProc:
    def __init__(self, *, alive: bool = True) -> None:
        self._alive = alive
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def terminate(self, *args: object) -> None:
        self.calls.append(("terminate", args))
        self._alive = False

    def close(self) -> None:
        self.calls.append(("close", ()))

    def isalive(self) -> bool:
        return self._alive


def _make_session(proc: _FakeProc) -> PtySession:
    session = object.__new__(PtySession)
    session._proc = proc
    return session


def test_windows_pty_teardown_closes_handle_after_terminate() -> None:
    proc = _FakeProc(alive=True)
    session = _make_session(proc)

    PtySession._terminate_process(session)

    assert ("terminate", (True,)) in proc.calls
    assert ("close", ()) in proc.calls


def test_windows_pty_teardown_closes_handle_even_if_process_is_already_dead() -> None:
    proc = _FakeProc(alive=False)
    session = _make_session(proc)

    PtySession._terminate_process(session)

    assert ("close", ()) in proc.calls
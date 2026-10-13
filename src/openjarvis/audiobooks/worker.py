"""The render worker: a thread in the server that works through the queue.

Live speech always wins (scope, 2026-10-13). Before every piece the worker
asks the server's own record of the conversation (``core.activity``, the
same signals that keep reminders from talking over a reply): while Sage
speaks, listens after a wake word, has a reply on its way, or the user
spoke in the last few seconds, it waits. A live reply that starts mid-piece
waits at most for that piece (median 0.9 s, longest 3.9 s on 10-13); a
spoken turn takes longer than that, so the GPU is normally free before the
reply is made. Rendering otherwise runs whenever Sage is idle (user's
choice, 2026-10-13: games and films included).

What it is doing is written to ``<data>/books/render-status.json`` for the
CLI now and the Books page later.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from openjarvis.audiobooks.check import WhisperTranscriber
from openjarvis.audiobooks.queue import RenderQueue
from openjarvis.audiobooks.render import ChapterRenderer, Stopped
from openjarvis.audiobooks.store import BookStore
from openjarvis.audiobooks.voiceproc import BookVoice, resource_problem, voice_exists

logger = logging.getLogger(__name__)

#: Seconds of quiet after the user's last turn / Sage's last reply before
#: rendering resumes: a follow-up question usually comes within this.
QUIET_AFTER_TURN = 10.0
QUIET_AFTER_REPLY = 6.0
POLL_SECONDS = 3.0
#: Stop the book voice after the queue has been empty this long.
STOP_IDLE_SECONDS = 120.0
#: After a failure, wait before trying again.
BACKOFF_SECONDS = 300.0
RESOURCE_RETRY_SECONDS = 60.0


def live_busy(now: Optional[float] = None) -> Optional[str]:
    """Why live Sage needs the GPU right now, or None when it does not."""
    from openjarvis.core import activity
    from openjarvis.speech import player

    now = time.time() if now is None else now
    if player.is_speaking():
        return "Sage is speaking"
    snap = activity.snapshot()
    if snap.sage_mid_turn:
        return "Sage is talking or listening"
    if snap.exchange_live:
        return "a reply is on its way"
    if snap.last_user_turn_at and now - snap.last_user_turn_at < QUIET_AFTER_TURN:
        return "you just spoke"
    if snap.last_reply_end_at and now - snap.last_reply_end_at < QUIET_AFTER_REPLY:
        return "Sage just replied"
    return None


class RenderWorker(threading.Thread):
    def __init__(
        self,
        store: Optional[BookStore] = None,
        voice: Optional[BookVoice] = None,
        busy: Callable[[], Optional[str]] = live_busy,
        transcriber: Any = "default",
        resources: Callable[[str], Optional[str]] = resource_problem,
        poll: float = POLL_SECONDS,
    ) -> None:
        super().__init__(name="audiobook-render", daemon=True)
        self.store = store or BookStore()
        self.queue = RenderQueue(self.store)
        self.voice = voice or BookVoice()
        self.busy = busy
        self.transcriber = (
            WhisperTranscriber() if transcriber == "default" else transcriber
        )
        self.resources = resources
        self.poll = poll
        self._halt = threading.Event()
        self._idle_since: Optional[float] = None
        self._retry_at = 0.0
        self.status: Dict[str, Any] = {}

    # ------------------------------------------------------------ status

    def _set_status(self, state: str, **fields: Any) -> None:
        status = {
            "state": state,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "book_voice_running": self.voice.running(),
            **fields,
        }
        if {k: v for k, v in status.items() if k != "updated"} == {
            k: v for k, v in self.status.items() if k != "updated"
        }:
            return
        self.status = status
        try:
            path = self.store.root / "render-status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.debug("could not write render status", exc_info=True)

    # ------------------------------------------------------------ loop

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        logger.info("audiobook render worker started")
        while not self._halt.is_set():
            try:
                self.step()
            except Exception:
                logger.exception("audiobook render worker failed a step")
                self._retry_at = time.monotonic() + BACKOFF_SECONDS
            self._halt.wait(self.poll)
        self.voice.stop()

    def step(self) -> None:
        """One look at the queue: render one chapter, or wait."""
        now = time.monotonic()
        data = self.queue.load()
        work = None if data["paused"] else self.queue.next_work()
        if work is None:
            self._idle_since = self._idle_since or now
            if self.voice.running() and now - self._idle_since > STOP_IDLE_SECONDS:
                self.voice.stop()
            self._set_status("paused" if data["paused"] else "idle")
            return
        self._idle_since = None
        if now < self._retry_at:
            return
        job, index = work
        voice = job["voice"]
        where = {
            "job": job["id"],
            "book": job["book"],
            "chapter": index,
            "voice": voice,
        }
        if not voice_exists(voice):
            self._set_status("error", reason=f"no voice {voice!r}", **where)
            self._retry_at = now + BACKOFF_SECONDS
            return
        if not self.voice.running():
            reason = self.busy()
            if reason:
                self._set_status("waiting", reason=reason, **where)
                return
            problem = self.resources(voice)
            if problem:
                self._set_status("waiting", reason=problem, **where)
                self._retry_at = now + RESOURCE_RETRY_SECONDS
                return
            self._set_status("starting", reason="loading the book voice", **where)
            try:
                self.voice.start(voice)
            except Exception as exc:
                logger.warning("book voice failed to start: %s", exc)
                self._set_status("error", reason=str(exc), **where)
                self._retry_at = now + BACKOFF_SECONDS
                return
        self._render(job, index, voice, where)

    def _may_go(
        self, job_id: str, where: Dict[str, Any], progress: Dict[str, int]
    ) -> bool:
        """Block while live Sage needs the GPU; False to stop this chapter."""
        while not self._halt.is_set():
            data = self.queue.load()
            if data["paused"] or not self.queue.active(job_id):
                return False
            reason = self.busy()
            if not reason:
                self._set_status("rendering", **where, **progress)
                return True
            self._set_status("waiting", reason=reason, **where, **progress)
            self._halt.wait(0.5)
        return False

    def _render(
        self, job: Dict[str, Any], index: int, voice: str, where: Dict[str, Any]
    ) -> None:
        progress = {"piece": 0, "pieces": 0}

        def on_progress(done: int, total: int) -> None:
            progress.update(piece=done, pieces=total)

        renderer = ChapterRenderer(
            self.store,
            self.voice.synthesize,
            lambda: self._may_go(job["id"], where, progress),
            transcriber=self.transcriber,
            on_progress=on_progress,
        )
        started = time.monotonic()
        try:
            result = renderer.render(job["book"], index, voice)
        except Stopped:
            self._set_status("stopped", **where, **progress)
            return
        except Exception as exc:
            logger.exception("rendering %s chapter %s failed", job["book"], index)
            if not self.voice.running():
                exc = RuntimeError(f"the book voice died: {exc}")
            self._set_status("error", reason=str(exc), **where)
            self.voice.stop()
            self._retry_at = time.monotonic() + BACKOFF_SECONDS
            return
        logger.info(
            "audiobook %s chapter %s rendered: %.0f s audio in %.0f s "
            "(%d retakes, %d flagged)",
            job["book"],
            index,
            result.seconds,
            time.monotonic() - started,
            result.retakes,
            len(result.flags),
        )
        self._set_status("rendered", last_seconds=result.seconds, **where)


_worker: Optional[RenderWorker] = None


def start_worker() -> RenderWorker:
    """Start the one render worker of this server (``jarvis serve``)."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = RenderWorker()
        _worker.start()
    return _worker


def stop_worker() -> None:
    if _worker is not None:
        _worker.stop()
        _worker.voice.stop()

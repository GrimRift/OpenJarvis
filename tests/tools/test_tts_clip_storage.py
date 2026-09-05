"""Generated speech clips live in one directory and are pruned by age.

Every synthesis used to call tempfile.mkdtemp, leaving a directory behind
forever: 377 had accumulated over sixteen days on the development machine.
Worse, once empty those directories could not be removed at all -- both
shutil.rmtree and PowerShell returned "Access is denied" on directories the
user owned -- so a sweep that deleted them was never going to work. The fix
is to stop creating them: one directory we keep, and files inside it, which
do delete.
"""

from __future__ import annotations

import os
import time

from openjarvis.tools.text_to_speech import (
    _CLIP_MAX_AGE_SECONDS,
    _clip_dir,
    _prune_old_clips,
)


def _aged(path, seconds_old: float) -> None:
    stamp = time.time() - seconds_old
    os.utime(path, (stamp, stamp))


class TestPruning:
    def test_a_clip_older_than_a_day_is_removed(self, tmp_path):
        stale = tmp_path / "clip-old.mp3"
        stale.write_bytes(b"x")
        _aged(stale, _CLIP_MAX_AGE_SECONDS + 60)

        _prune_old_clips(tmp_path)
        assert not stale.exists()

    def test_a_recent_clip_survives(self, tmp_path):
        """A clip the current session may still be playing."""
        fresh = tmp_path / "clip-new.mp3"
        fresh.write_bytes(b"x")

        _prune_old_clips(tmp_path)
        assert fresh.exists()

    def test_a_directory_is_never_removed(self, tmp_path):
        """Only files are pruned. Directories are what could not be deleted."""
        nested = tmp_path / "jarvis-tts-legacy"
        nested.mkdir()
        _aged(nested, _CLIP_MAX_AGE_SECONDS * 10)

        _prune_old_clips(tmp_path)
        assert nested.exists()

    def test_an_unreadable_directory_is_not_an_error(self, tmp_path):
        """Pruning must never fail a synthesis the user is waiting on."""
        _prune_old_clips(tmp_path / "does-not-exist")


class TestTheClipDirectory:
    def test_it_is_one_stable_directory(self):
        assert _clip_dir() == _clip_dir()
        assert _clip_dir().name == "jarvis-tts"

    def test_it_exists_after_being_asked_for(self):
        assert _clip_dir().is_dir()

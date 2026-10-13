"""Rendering one chapter: pieces -> takes -> check -> one M4A.

Every finished piece is saved before the next is asked for
(``audio/<voice>/work/<NNNN>/``), so a restart, a crash or a pause for live
speech resumes where it stopped instead of re-rendering the chapter. Every
piece's length is checked, and short pieces are also listened back by
Whisper (``check.py``); a piece that comes out repeated, garbled, too short
or too long gets up to two more takes and the best one is kept. Then the
chapter is joined with its pauses, levelled to
-16 LUFS like Sage's other clips, and encoded as AAC 64 kbps M4A (user's
choice, 2026-10-13: M4B parts are later built by stream copy and Telegram
can send a chapter as it is).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from openjarvis.audiobooks import pieces as piece_rules
from openjarvis.audiobooks.check import Transcriber, chapter_rate, judge
from openjarvis.audiobooks.queue import audio_dir
from openjarvis.audiobooks.store import BookStore
from openjarvis.speech.spoken_text import to_spoken_text

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
#: Extra takes for a piece the check rejects.
RETAKES = 2
#: Whisper listens only to pieces shorter than this (user, 2026-10-13: the
#: full check kept 2 CPU threads busy). Short lines are where Turbo repeats
#: itself; every piece still gets the free length checks (near-silent, run-on).
LISTEN_UNDER_CHARS = 40
LOUDNESS = -16.0
BITRATE = "64k"


class Stopped(Exception):
    """Rendering was asked to stop (pause, cancel, shutdown) mid-chapter."""


#: Text -> float32 mono audio at SAMPLE_RATE, in the given voice.
Synthesize = Callable[[str, str], np.ndarray]
#: Called before every piece; blocks while live Sage needs the GPU and
#: returns False when rendering should stop.
MayGo = Callable[[], bool]


@dataclass
class ChapterResult:
    index: int
    path: Path
    seconds: float
    pieces: int
    gen_seconds: float
    retakes: int
    flags: List[Dict[str, Any]] = field(default_factory=list)
    #: [paragraph, start second] for every paragraph that has sound or a
    #: pause, in order: the reader lights up the one being read.
    paragraphs: List[List[float]] = field(default_factory=list)


def paragraph_starts(
    items: List[piece_rules.Item], starts: List[float]
) -> List[List[float]]:
    """[paragraph, second it starts] from the items and their start times."""
    out: List[List[float]] = []
    for item, start in zip(items, starts):
        if item.para >= 0 and (not out or out[-1][0] != item.para):
            out.append([item.para, round(start, 2)])
    return out


def _write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    tmp = path.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on PATH; it is needed to encode chapters")
    return exe


def encode_m4a(wav: Path, out: Path, tags: Dict[str, str]) -> None:
    """Two-pass loudnorm to LOUDNESS (linear, so no pumping), AAC, tagged."""
    ffmpeg = _ffmpeg()
    probe = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav),
            "-af",
            f"loudnorm=I={LOUDNESS}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    blob = probe.stderr[probe.stderr.rindex("{") : probe.stderr.rindex("}") + 1]
    m = json.loads(blob)
    measured = (
        f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
        f"offset={m['target_offset']}:linear=true"
    )
    meta: List[str] = []
    for key, value in tags.items():
        meta += ["-metadata", f"{key}={value}"]
    tmp = out.with_name(out.stem + ".part.m4a")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav),
            "-af",
            f"loudnorm=I={LOUDNESS}:TP=-1.5:LRA=11:{measured},aresample={SAMPLE_RATE}",
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            BITRATE,
            "-movflags",
            "+faststart",
            *meta,
            str(tmp),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tmp.replace(out)


class ChapterRenderer:
    def __init__(
        self,
        store: BookStore,
        synthesize: Synthesize,
        may_go: MayGo,
        transcriber: Optional[Transcriber] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.store = store
        self.synthesize = synthesize
        self.may_go = may_go
        self.transcriber = transcriber
        self.on_progress = on_progress or (lambda done, total: None)

    def _take(self, text: str, voice: str) -> tuple:
        if not self.may_go():
            raise Stopped()
        started = time.monotonic()
        audio = self.synthesize(text, voice)
        return audio, time.monotonic() - started

    def render(self, book_id: str, index: int, voice: str) -> ChapterResult:
        book = self.store.load(book_id)
        chapter = next(c for c in book["chapters"] if c["index"] == index)
        text = self.store.chapter_text(book_id, index)
        # The same last pass as every live reply (an architecture invariant):
        # dashes become commas, and stray markup or codes are not read out.
        items = []
        for it in piece_rules.build(text, piece_rules.announcement(chapter)):
            spoken = ""
            if it.text:
                spoken = to_spoken_text(piece_rules.spoken_stutters(it.text)).strip()
            items.append(piece_rules.Item(spoken or None, it.gap, it.para))
        folder = audio_dir(self.store, book_id, voice)
        work = folder / "work" / f"{index:04d}"
        plan = [it.text for it in items]
        digest = hashlib.sha256(json.dumps([plan, voice]).encode()).hexdigest()[:16]
        manifest_path = work / "manifest.json"
        manifest: Dict[str, Any] = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("digest") != digest:
            # The text, the piece rules or the voice changed: start over.
            shutil.rmtree(work, ignore_errors=True)
            manifest = {"digest": digest, "pieces": {}}
        work.mkdir(parents=True, exist_ok=True)

        def save_manifest() -> None:
            tmp = manifest_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest), encoding="utf-8")
            tmp.replace(manifest_path)

        spoken = [k for k, it in enumerate(items) if it.text is not None]
        done = lambda: sum(1 for k in spoken if str(k) in manifest["pieces"])  # noqa: E731
        # Listening back runs on the CPU while the next pieces render on the
        # GPU: in series it was a third of the chapter's time (10-13 e2e run).
        ears = ThreadPoolExecutor(1) if self.transcriber is not None else None
        heard: Dict[int, Future] = {}
        try:
            for k in spoken:
                if str(k) in manifest["pieces"]:
                    continue
                audio, gen = self._take(items[k].text, voice)
                _write_wav(work / f"p{k:04d}.wav", audio)
                manifest["pieces"][str(k)] = {
                    "seconds": len(audio) / SAMPLE_RATE,
                    "gen": gen,
                }
                if ears is not None and self._listens_to(items[k].text):
                    heard[k] = ears.submit(self._listen, items[k].text, audio)
                elif ears is not None:
                    manifest["pieces"][str(k)]["heard"] = None
                self._collect(heard, manifest)
                save_manifest()
                self.on_progress(done(), len(spoken))
            for future in heard.values():
                future.result()
            self._collect(heard, manifest)
            save_manifest()
        finally:
            if ears is not None:
                ears.shutdown(wait=False, cancel_futures=True)

        retakes, flags = self._check(
            items, spoken, work, manifest, voice, save_manifest
        )

        joined: List[np.ndarray] = []
        starts: List[float] = []
        samples = 0
        for k, item in enumerate(items):
            starts.append(samples / SAMPLE_RATE)
            if item.text is not None:
                joined.append(_read_wav(work / f"p{k:04d}.wav"))
                samples += len(joined[-1])
            joined.append(np.zeros(int(SAMPLE_RATE * item.gap), np.float32))
            samples += len(joined[-1])
        audio = np.concatenate(joined) if joined else np.zeros(0, np.float32)
        for flag in flags:
            flag["at"] = round(starts[flag["piece"]], 2)
        out = folder / f"{index:04d}.m4a"
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "chapter.wav"
            _write_wav(wav, audio)
            encode_m4a(
                wav,
                out,
                {
                    "title": piece_rules.announcement(chapter).rstrip("."),
                    "album": book["title"],
                    "artist": book.get("author") or "",
                    "track": str(index),
                    "comment": f"Sage voice: {voice}",
                },
            )
        gen_total = sum(p["gen"] for p in manifest["pieces"].values())
        result = ChapterResult(
            index=index,
            path=out,
            seconds=round(len(audio) / SAMPLE_RATE, 1),
            pieces=len(spoken),
            gen_seconds=round(gen_total, 1),
            retakes=retakes,
            flags=flags,
            paragraphs=paragraph_starts(items, starts),
        )
        self._record(folder, result)
        shutil.rmtree(work, ignore_errors=True)
        if work.parent.is_dir() and not any(work.parent.iterdir()):
            work.parent.rmdir()
        return result

    @staticmethod
    def _collect(heard: Dict[int, Future], manifest: Dict[str, Any]) -> None:
        """Move finished transcriptions into the manifest (main thread only)."""
        for k in [k for k, f in heard.items() if f.done()]:
            manifest["pieces"][str(k)]["heard"] = heard.pop(k).result()

    def _check(self, items, spoken, work, manifest, voice, save_manifest):
        """Listen to every piece; re-take the ones that fail. Returns
        (retakes made, pieces still failing after their last take)."""
        rate = chapter_rate(
            [(items[k].text, manifest["pieces"][str(k)]["seconds"]) for k in spoken]
        )
        retakes = 0
        flags: List[Dict[str, Any]] = []
        for k in spoken:
            entry = manifest["pieces"][str(k)]
            if entry.get("checked"):
                if entry.get("flags"):
                    flags.append(
                        {"piece": k, "text": items[k].text, "why": entry["flags"]}
                    )
                continue
            text = items[k].text
            if "heard" in entry:
                best = judge(text, entry["heard"], entry["seconds"], rate)
            else:  # rendered before a restart: not listened to yet
                best = self._judge(text, _read_wav(work / f"p{k:04d}.wav"), rate)
            tries = entry.get("takes", 1)
            while best and tries <= RETAKES:
                audio, gen = self._take(text, voice)
                tries += 1
                retakes += 1
                entry["gen"] += gen
                reasons = self._judge(text, audio, rate)
                if len(reasons) < len(best):
                    _write_wav(work / f"p{k:04d}.wav", audio)
                    entry["seconds"] = len(audio) / SAMPLE_RATE
                    best = reasons
                entry["takes"] = tries
                save_manifest()
            entry.update(checked=True, flags=best)
            save_manifest()
            if best:
                flags.append({"piece": k, "text": text, "why": best})
        return retakes, flags

    def _listens_to(self, text: str) -> bool:
        return self.transcriber is not None and len(text) < LISTEN_UNDER_CHARS

    def _listen(self, text: str, audio: np.ndarray) -> Optional[str]:
        """What Whisper hears in a short piece; None for long ones (length
        checks only) or when it fails."""
        if not self._listens_to(text):
            return None
        try:
            return self.transcriber.transcribe(audio, SAMPLE_RATE)
        except Exception:  # the check is a safety net, not a gate
            logger.warning("listening back to a piece failed", exc_info=True)
            return None

    def _judge(self, text: str, audio: np.ndarray, rate: float) -> List[str]:
        heard = self._listen(text, audio)
        return judge(text, heard, len(audio) / SAMPLE_RATE, rate)

    def _record(self, folder: Path, result: ChapterResult) -> None:
        path = folder / "status.json"
        status = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        status[str(result.index)] = {
            "file": result.path.name,
            "seconds": result.seconds,
            "pieces": result.pieces,
            "gen_seconds": result.gen_seconds,
            "rtf": round(result.gen_seconds / result.seconds, 3)
            if result.seconds
            else None,
            "retakes": result.retakes,
            "flags": result.flags,
            "paragraphs": result.paragraphs,
            "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(status, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

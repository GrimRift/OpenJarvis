"""Approved Cartesia voice profiles used by Sage."""

from __future__ import annotations

from dataclasses import dataclass

CARTESIA_API_VERSION = "2026-03-01"
DEFAULT_TTS_MODEL = "sonic-3.6"


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    name: str
    voice_id: str
    speed: float
    volume: float


JARVIS = VoiceProfile(
    name="Jarvis",
    voice_id="78a05d7d-268b-4a18-aad7-7a96902a95ee",
    speed=1.0,
    volume=1.9,
)
FRIEREN = VoiceProfile(
    name="Frieren",
    voice_id="e23c9ecf-e002-4f7a-8e39-13d18d09923f",
    speed=0.9,
    volume=1.9,
)

VOICE_PROFILES = (JARVIS, FRIEREN)
DEFAULT_VOICE = JARVIS


__all__ = [
    "CARTESIA_API_VERSION",
    "DEFAULT_TTS_MODEL",
    "DEFAULT_VOICE",
    "FRIEREN",
    "JARVIS",
    "VOICE_PROFILES",
    "VoiceProfile",
]

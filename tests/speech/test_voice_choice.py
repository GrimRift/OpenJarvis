"""The Settings choice reaches server-side speech; config.toml is the default."""

from openjarvis.core.config import JarvisConfig
from openjarvis.speech.voice_choice import (
    VoiceChoice,
    chosen_provider,
    chosen_voice_id,
    load_choice,
    save_choice,
)


def test_config_is_the_default_when_nothing_was_chosen(tmp_path):
    cfg = JarvisConfig().speech
    assert chosen_provider(cfg, tmp_path) == "cartesia"
    assert chosen_voice_id(cfg, tmp_path) == cfg.voice_id
    cfg.tts_provider = "chatterbox"
    assert chosen_voice_id(cfg, tmp_path) == "chatterbox:jarvis"


def test_the_settings_choice_wins_and_survives_a_reload(tmp_path):
    cfg = JarvisConfig().speech
    save_choice(VoiceChoice("chatterbox", "chatterbox:butler"), tmp_path)
    assert load_choice(tmp_path) == VoiceChoice("chatterbox", "chatterbox:butler")
    assert chosen_provider(cfg, tmp_path) == "chatterbox"
    assert chosen_voice_id(cfg, tmp_path) == "chatterbox:butler"


def test_a_voice_from_the_other_engine_is_not_used(tmp_path):
    # Switched back to Cartesia with the local voice still stored: moments
    # must not hand a chatterbox id to Cartesia.
    cfg = JarvisConfig().speech
    save_choice(VoiceChoice("cartesia", "chatterbox:jarvis"), tmp_path)
    assert chosen_voice_id(cfg, tmp_path) == cfg.voice_id


def test_garbage_in_the_file_falls_back(tmp_path):
    (tmp_path / "voice_choice.json").write_text('{"tts_provider": "kokoro"}')
    assert load_choice(tmp_path) == VoiceChoice()
    (tmp_path / "voice_choice.json").write_text("not json")
    assert load_choice(tmp_path) == VoiceChoice()

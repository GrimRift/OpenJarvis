"""Editing the llama.cpp router's presets from Settings."""

from __future__ import annotations

import pytest

from openjarvis.engine.llamacpp_presets import read_presets, update_presets

PRESETS = """; Sage's local models
version = 1

[*]
flash-attn = on
sleep-idle-seconds = 180

; default model
[qwen3.5-9b]
model = D:\\models\\9b.gguf
ctx-size = 12288

[qwen3.6-35b-a3b]
model = D:\\models\\35b.gguf
n-cpu-moe = 33
"""


@pytest.fixture
def presets(tmp_path):
    path = tmp_path / "models.ini"
    path.write_text(PRESETS, encoding="utf-8")
    return path


def test_changes_a_value_in_place_and_keeps_comments(presets):
    update_presets(presets, {"qwen3.5-9b": {"ctx-size": 8192}})
    text = presets.read_text(encoding="utf-8")
    assert "ctx-size = 8192" in text
    assert "; default model" in text
    assert read_presets(presets)["qwen3.5-9b"]["model"] == "D:\\models\\9b.gguf"


def test_adds_a_missing_key_to_its_own_section(presets):
    update_presets(presets, {"qwen3.5-9b": {"cache-type-k": "q8_0"}})
    values = read_presets(presets)
    assert values["qwen3.5-9b"]["cache-type-k"] == "q8_0"
    assert "cache-type-k" not in values["qwen3.6-35b-a3b"]


def test_shared_idle_time(presets):
    update_presets(presets, {"*": {"sleep-idle-seconds": 600}})
    assert read_presets(presets)["*"]["sleep-idle-seconds"] == "600"


@pytest.mark.parametrize(
    "changes",
    [
        {"qwen3.5-9b": {"model": "C:\\evil.gguf"}},  # not editable
        {"qwen3.5-9b": {"ctx-size": 999999}},  # out of range
        {"qwen3.5-9b": {"cache-type-k": "f64"}},  # not a choice
        {"nope": {"ctx-size": 4096}},  # no such model
        {"*": {"ctx-size": 4096}},  # not a shared setting
    ],
)
def test_rejects_bad_changes_and_writes_nothing(presets, changes):
    with pytest.raises(ValueError):
        update_presets(presets, changes)
    assert presets.read_text(encoding="utf-8") == PRESETS


def test_never_writes_a_byte_order_mark(presets):
    """The router cannot parse a presets file that starts with one."""
    update_presets(presets, {"qwen3.5-9b": {"ctx-size": 4096}})
    assert not presets.read_bytes().startswith(b"\xef\xbb\xbf")

"""Nano and Turbo in one sidecar: each voice belongs to a model, and choosing
the voice loads its model."""

from __future__ import annotations

import json

import pytest

engine_mod = pytest.importorskip("voice_sidecar.engine")


def _voice(root, name, meta=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "reference.wav").write_bytes(b"RIFF")
    if meta is not None:
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


class TestVoiceMeta:
    def test_a_voice_without_meta_is_a_nano_voice(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        _voice(tmp_path, "jarvis")
        assert store.meta("jarvis") == {
            "engine": "nano",
            "label": "",
            "orb": {"gain": 1.0, "contrast": 1.0},
        }

    def test_meta_is_kept_apart_from_the_sampling(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        _voice(tmp_path, "turbo-jarvis")
        store.save_meta("turbo-jarvis", engine="turbo", label="J.A.R.V.I.S.")
        # Rewriting the sampling whole must not lose which model it is.
        store.save_params("turbo-jarvis", engine_mod.VoiceParams(temperature=0.65))
        assert store.engine("turbo-jarvis") == "turbo"
        info = store.info("turbo-jarvis")
        assert (info.engine, info.label) == ("turbo", "J.A.R.V.I.S.")
        assert info.params["temperature"] == 0.65

    def test_an_unknown_model_is_refused_and_a_bad_file_reads_as_nano(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        folder = _voice(tmp_path, "odd")
        with pytest.raises(ValueError):
            store.save_meta("odd", engine="huge")
        (folder / "meta.json").write_text("not json", encoding="utf-8")
        assert store.engine("odd") == "nano"
        (folder / "meta.json").write_text('{"engine": "huge"}', encoding="utf-8")
        assert store.engine("odd") == "nano"


class TestOrbShaping:
    def test_a_voice_without_one_is_neutral(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        _voice(tmp_path, "jarvis", {"engine": "nano", "label": "Jarvis"})
        assert store.meta("jarvis")["orb"] == {"gain": 1.0, "contrast": 1.0}

    def test_the_fitted_pair_is_read_and_bounded(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        _voice(
            tmp_path, "t", {"engine": "turbo", "orb": {"gain": 1.2, "contrast": 1.5}}
        )
        _voice(tmp_path, "wild", {"orb": {"gain": 99, "contrast": "loud"}})
        assert store.info("t").orb == {"gain": 1.2, "contrast": 1.5}
        assert store.meta("wild")["orb"] == {"gain": 3.0, "contrast": 1.0}

    def test_saving_the_name_keeps_the_shaping(self, tmp_path):
        store = engine_mod.VoiceStore(tmp_path)
        _voice(
            tmp_path, "t", {"engine": "turbo", "orb": {"gain": 1.2, "contrast": 1.5}}
        )
        store.save_meta("t", label="J.A.R.V.I.S.")
        assert store.meta("t")["orb"] == {"gain": 1.2, "contrast": 1.5}


class FakeLoading(engine_mod.ChatterboxEngine):
    """The engine with the model load and the conditioning stubbed out."""

    def __init__(self, store, model="nano"):
        super().__init__("cpu", store, model=model)
        self.loads = []
        self.conditioned = []
        self.warmed = []

    def load(self):
        self.loads.append(self.kind)
        self.model = object()
        self.fast_t3 = f"graphs for {self.kind}"

    def _use_conditioning(self, name):
        self.conditioned.append(name)
        self.current_voice = name

    def warm_up(self, voice):
        self.warmed.append(voice)


@pytest.fixture
def voices(tmp_path):
    store = engine_mod.VoiceStore(tmp_path)
    _voice(tmp_path, "jarvis", {"engine": "nano", "label": "Jarvis"})
    _voice(tmp_path, "frieren", {"engine": "nano", "label": "Frieren"})
    _voice(tmp_path, "turbo-jarvis", {"engine": "turbo", "label": "J.A.R.V.I.S."})
    return store


class TestSwitching:
    def test_choosing_a_voice_of_the_other_model_loads_that_model(self, voices):
        eng = FakeLoading(voices)
        eng.load()
        eng.use_voice("jarvis")
        assert eng.loads == ["nano"]
        eng.use_voice("turbo-jarvis")
        assert eng.kind == "turbo" and eng.loads == ["nano", "turbo"]
        # The first line in the new model is warmed, so the reply that
        # follows does not pay for the graph capture.
        assert eng.warmed == ["turbo-jarvis"]
        assert eng.fast_t3 == "graphs for turbo"

    def test_a_voice_of_the_same_model_does_not_reload(self, voices):
        eng = FakeLoading(voices)
        eng.load()
        eng.use_voice("jarvis")
        eng.use_voice("frieren")
        assert eng.loads == ["nano"] and eng.warmed == []
        assert eng.conditioned == ["jarvis", "frieren"]

    def test_a_switch_drops_what_belonged_to_the_old_model(self, voices):
        eng = FakeLoading(voices)
        eng.load()
        eng.use_voice("jarvis")
        eng._ve = "nano's voice encoder copy"
        eng.switch("turbo")
        assert eng._ve is None and eng.current_voice is None
        assert eng.switch_seconds >= 0.0

    def test_an_unknown_model_is_refused(self, voices):
        eng = FakeLoading(voices)
        with pytest.raises(ValueError):
            eng.switch("huge")

    def test_the_memory_cap_is_the_models_own(self):
        assert (
            engine_mod.MEMORY_FRACTIONS["turbo"] > engine_mod.MEMORY_FRACTIONS["nano"]
        )
        assert set(engine_mod.MEMORY_FRACTIONS) == set(engine_mod.MODELS)

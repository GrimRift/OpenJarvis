"""M40 image tools: create, edit/refine, send to phone; storage and cost."""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjarvis.images import attachments
from openjarvis.images import settings as image_settings
from openjarvis.images.service import ImageService, Usage, cost_usd, usage_from
from openjarvis.images.store import ImageStore, slug, unique_path
from openjarvis.tools import image_tool
from openjarvis.tools.image_tool import (
    ImageEditTool,
    ImageGenerateTool,
    ImageToPhoneTool,
)

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _response(png: bytes = PNG, out: int = 439, text_in: int = 30, image_in: int = 0):
    usage = SimpleNamespace(
        model_dump=lambda: {
            "input_tokens": text_in + image_in,
            "input_tokens_details": {"text_tokens": text_in, "image_tokens": image_in},
            "output_tokens": out,
        }
    )
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png).decode())], usage=usage
    )


class _FakeImages:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    def generate(self, **kw):
        self.calls.append(("generate", kw))
        if self.error:
            raise self.error
        return _response()

    def edit(self, **kw):
        kw = dict(kw)
        kw["image_bytes"] = kw.pop("image").read()
        self.calls.append(("edit", kw))
        if self.error:
            raise self.error
        return _response(image_in=1024)


@pytest.fixture
def rig(tmp_path):
    images = _FakeImages()
    telemetry: list[tuple] = []
    cfg = image_settings.ImageSettings(save_dir=str(tmp_path / "Pictures"))
    service = ImageService(
        store=ImageStore(tmp_path / "index.db"),
        client_factory=lambda: SimpleNamespace(images=images),
        record_telemetry=lambda *a: telemetry.append(a),
        settings_loader=lambda: cfg,
    )
    attachments.clear()
    yield SimpleNamespace(
        images=images, telemetry=telemetry, service=service, cfg=cfg, tmp=tmp_path
    )
    attachments.clear()


class TestNamingAndStorage:
    def test_slug_keeps_the_meaningful_words(self):
        assert slug("Draw me a cozy cafe at sunset, please!") == "cozy-cafe-sunset"

    def test_slug_never_empty(self):
        assert slug("!!!") == "image"

    def test_a_taken_name_gets_a_suffix_never_an_overwrite(self, tmp_path):
        (tmp_path / "2026-09-29_cafe.png").write_bytes(b"old")
        (tmp_path / "2026-09-29_cafe-2.png").write_bytes(b"old")
        assert (
            unique_path(tmp_path, "2026-09-29", "cafe").name == "2026-09-29_cafe-3.png"
        )

    def test_save_writes_the_file_and_indexes_it(self, tmp_path):
        store = ImageStore(tmp_path / "index.db")
        rec = store.save(
            PNG,
            folder=tmp_path / "Pics",
            prompt="a red owl",
            kind="generate",
            model="m",
            quality="medium",
            size="1024x1024",
            cost_usd=0.01,
            now=datetime(2026, 9, 29, 12, 0),
        )
        assert Path(rec.path).name == "2026-09-29_red-owl.png"
        assert Path(rec.path).read_bytes() == PNG
        assert store.get(rec.id).path == rec.path
        assert store.latest().id == rec.id


class TestCost:
    def test_cost_comes_from_returned_usage_and_published_price(self):
        usage = Usage(text_in=34, image_in=0, out=439)
        assert cost_usd("gpt-image-2.5-flare", usage) == pytest.approx(0.0133, abs=1e-4)

    def test_an_unpriced_model_is_unknown_not_free(self):
        assert cost_usd("gpt-image-9", Usage(out=100)) is None

    def test_no_usage_is_unknown_not_free(self):
        assert cost_usd("gpt-image-2", None) is None
        assert usage_from(SimpleNamespace()) is None


class TestSettings:
    def test_defaults_are_the_users_benchmark_choice(self, tmp_path):
        cfg = image_settings.load(tmp_path / "none.json")
        assert (cfg.model, cfg.quality, cfg.enabled) == (
            "gpt-image-2.5-flare",
            "medium",
            True,
        )

    def test_update_persists_and_rejects_bad_values(self, tmp_path):
        path = tmp_path / "s.json"
        image_settings.update({"quality": "high"}, path)
        assert image_settings.load(path).quality == "high"
        with pytest.raises(ValueError):
            image_settings.update({"model": "dall-e-2"}, path)
        assert image_settings.load(path).quality == "high"

    def test_a_corrupt_file_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{not json")
        assert image_settings.load(path) == image_settings.ImageSettings()


class TestAttachments:
    def test_the_newest_user_turn_image_is_available(self):
        data = "data:image/png;base64," + base64.b64encode(PNG).decode()
        attachments.set_turn([{"role": "user", "content": "x", "images": [data]}])
        assert attachments.attached_png() == PNG

    def test_a_turn_without_an_image_clears_the_last_one(self):
        attachments.set_turn(
            [{"role": "user", "images": [base64.b64encode(PNG).decode()]}]
        )
        attachments.set_turn([{"role": "user", "content": "and now?"}])
        assert attachments.attached_png() is None

    def test_an_older_turns_image_is_not_this_turns(self):
        old = base64.b64encode(PNG).decode()
        attachments.set_turn(
            [
                {"role": "user", "images": [old]},
                {"role": "assistant", "content": "a red square"},
                {"role": "user", "content": "make it blue"},
            ]
        )
        assert attachments.attached_png() is None


class TestGenerate:
    def test_creates_saves_and_records_cost(self, rig):
        result = ImageGenerateTool(rig.service).execute(prompt="a cafe at sunset")
        assert result.success
        meta = result.metadata["image"]
        assert meta["url"] == f"/v1/images/{meta['id']}"
        assert meta["cost_usd"] == pytest.approx(0.0133, abs=1e-4)
        assert (rig.tmp / "Pictures" / meta["file"]).read_bytes() == PNG
        call = rig.images.calls[0][1]
        assert (call["model"], call["quality"]) == ("gpt-image-2.5-flare", "medium")
        assert rig.telemetry and rig.telemetry[0][0] == "gpt-image-2.5-flare"

    def test_the_result_text_names_the_id_and_carries_no_image_data(self, rig):
        result = ImageGenerateTool(rig.service).execute(prompt="a cafe")
        assert result.metadata["image"]["id"] in result.content
        assert base64.b64encode(PNG).decode() not in result.content
        assert "b64" not in json.dumps(result.metadata)
        assert len(result.content) < 500  # survives the replay cut

    def test_a_refusal_is_reported_once_not_retried(self, rig):
        rig.images.error = RuntimeError(
            "Your request was rejected by the safety system"
        )
        result = ImageGenerateTool(rig.service).execute(prompt="x")
        assert not result.success
        assert "rejected by the safety system" in result.content
        assert len(rig.images.calls) == 1
        assert not rig.telemetry

    def test_turned_off_in_settings(self, rig):
        rig.cfg.enabled = False
        result = ImageGenerateTool(rig.service).execute(prompt="x")
        assert not result.success and "Settings > Images" in result.content
        assert not rig.images.calls

    def test_outlasts_the_default_tool_timeout(self):
        assert ImageGenerateTool().spec.timeout_seconds >= 120


class TestEdit:
    def test_edits_the_image_pasted_this_turn(self, rig):
        attachments.set_turn(
            [{"role": "user", "images": [base64.b64encode(b"PASTED").decode()]}]
        )
        result = ImageEditTool(rig.service).execute(
            instruction="sunset sky", image="attached"
        )
        assert result.success
        kind, call = rig.images.calls[0]
        assert kind == "edit" and call["image_bytes"] == b"PASTED"
        assert result.metadata["image"]["parent_id"] is None

    def test_a_picture_pasted_this_turn_wins_over_last(self, rig):
        """Live 2026-09-29: pasted a photo, said "make the sky a starry night",
        and the model passed image='last' -- Sage edited its previous picture
        instead of the one just pasted."""
        ImageGenerateTool(rig.service).execute(prompt="an owl")
        attachments.set_turn(
            [{"role": "user", "images": [base64.b64encode(b"PASTED").decode()]}]
        )
        result = ImageEditTool(rig.service).execute(
            instruction="starry night sky", image="last"
        )
        assert result.success
        assert rig.images.calls[-1][1]["image_bytes"] == b"PASTED"
        assert result.metadata["image"]["parent_id"] is None

    def test_an_explicit_id_still_wins_over_a_paste(self, rig):
        first = ImageGenerateTool(rig.service).execute(prompt="an owl")
        attachments.set_turn(
            [{"role": "user", "images": [base64.b64encode(b"PASTED").decode()]}]
        )
        image_id = first.metadata["image"]["id"]
        ImageEditTool(rig.service).execute(instruction="blue", image=image_id)
        assert rig.images.calls[-1][1]["image_bytes"] == PNG

    def test_attached_with_nothing_attached_does_not_edit_something_else(self, rig):
        ImageGenerateTool(rig.service).execute(prompt="an owl")
        result = ImageEditTool(rig.service).execute(
            instruction="blue", image="attached"
        )
        assert not result.success and "No image is attached" in result.content
        assert [k for k, _ in rig.images.calls] == ["generate"]

    def test_refine_last_uses_the_newest_file_and_links_the_parent(self, rig):
        first = (
            ImageGenerateTool(rig.service).execute(prompt="an owl").metadata["image"]
        )
        result = ImageEditTool(rig.service).execute(
            instruction="brighter", image="last"
        )
        assert result.success
        assert rig.images.calls[1][1]["image_bytes"] == PNG
        assert result.metadata["image"]["parent_id"] == first["id"]

    def test_refine_by_id_after_a_reload(self, rig, tmp_path):
        first = (
            ImageGenerateTool(rig.service).execute(prompt="an owl").metadata["image"]
        )
        fresh = ImageService(  # a new process: only the index on disk remains
            store=ImageStore(tmp_path / "index.db"),
            client_factory=lambda: SimpleNamespace(images=rig.images),
            record_telemetry=lambda *a: None,
            settings_loader=lambda: rig.cfg,
        )
        result = ImageEditTool(fresh).execute(
            instruction="anime style", image=first["id"]
        )
        assert result.success and result.metadata["image"]["parent_id"] == first["id"]

    def test_transparent_background_is_requested(self, rig):
        ImageGenerateTool(rig.service).execute(prompt="an owl")
        ImageEditTool(rig.service).execute(
            instruction="remove the background",
            image="last",
            transparent_background=True,
        )
        assert rig.images.calls[1][1]["background"] == "transparent"

    def test_unknown_id(self, rig):
        result = ImageEditTool(rig.service).execute(instruction="x", image="img_nope")
        assert not result.success and "img_nope" in result.content


class TestToPhone:
    def test_sends_the_newest_picture(self, rig, monkeypatch):
        sent = []
        monkeypatch.setattr(
            image_tool,
            "send_photo_to_phone",
            lambda path, caption: sent.append((path, caption)) or (True, ""),
        )
        rec = ImageGenerateTool(rig.service).execute(prompt="an owl").metadata["image"]
        result = ImageToPhoneTool(rig.service).execute(image="last")
        assert result.success
        assert Path(sent[0][0]).name == rec["file"] and sent[0][1] == "an owl"

    def test_a_failed_send_is_not_reported_as_sent(self, rig, monkeypatch):
        monkeypatch.setattr(
            image_tool,
            "send_photo_to_phone",
            lambda path, caption: (False, "Telegram refused"),
        )
        ImageGenerateTool(rig.service).execute(prompt="an owl")
        result = ImageToPhoneTool(rig.service).execute()
        assert not result.success and "Telegram refused" in result.content


class TestTelegramSendPhoto:
    def test_posts_multipart_send_photo(self, tmp_path, monkeypatch):
        import httpx

        from openjarvis.channels.telegram import TelegramChannel

        posted = {}

        def fake_post(url, data=None, files=None, timeout=None):
            posted.update(
                url=url,
                data=data,
                name=files["photo"][0],
                body=files["photo"][1].read(),
            )
            return SimpleNamespace(status_code=200, text="ok")

        monkeypatch.setattr(httpx, "post", fake_post)
        photo = tmp_path / "p.png"
        photo.write_bytes(PNG)
        channel = TelegramChannel(bot_token="t0k")
        assert channel.send_photo("123", str(photo), caption="owl") is True
        assert posted["url"].endswith("/sendPhoto")
        assert posted["data"] == {"chat_id": "123", "caption": "owl"}
        assert posted["body"] == PNG


class TestRoutes:
    def _client(self, tmp_path, store):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from openjarvis.server.image_routes import create_image_router

        app = FastAPI()
        app.include_router(
            create_image_router(
                store=store,
                settings_path=tmp_path / "s.json",
                telemetry_db=str(tmp_path / "telemetry.db"),
            )
        )
        return TestClient(app)

    def test_serves_the_png_by_id(self, rig, tmp_path):
        rec = ImageGenerateTool(rig.service).execute(prompt="owl").metadata["image"]
        client = self._client(tmp_path, rig.service.store)
        resp = client.get(rec["url"])
        assert resp.status_code == 200 and resp.content == PNG
        assert client.get("/v1/images/img_missing").status_code == 404

    def test_settings_round_trip_and_validation(self, tmp_path):
        client = self._client(tmp_path, ImageStore(tmp_path / "i.db"))
        assert (
            client.get("/v1/images/settings").json()["model"] == "gpt-image-2.5-flare"
        )
        assert (
            client.put("/v1/images/settings", json={"quality": "high"}).json()[
                "quality"
            ]
            == "high"
        )
        assert (
            client.put("/v1/images/settings", json={"size": "9x9"}).status_code == 400
        )

    def test_spend_breaks_out_images_and_counts_unknowns(self, tmp_path):
        from openjarvis.core.types import TelemetryRecord
        from openjarvis.server.image_routes import spend
        from openjarvis.telemetry.store import TelemetryStore

        store = TelemetryStore(tmp_path / "telemetry.db", batch_size=1)
        store.record(
            TelemetryRecord(timestamp=1000.0, model_id="gpt-6-luna", cost_usd=0.02)
        )
        store.record(
            TelemetryRecord(
                timestamp=1000.0,
                model_id="gpt-image-2.5-flare",
                engine="openai-images",
                cost_usd=0.013,
                metadata={"cost_known": True},
            )
        )
        store.record(
            TelemetryRecord(
                timestamp=1000.0,
                model_id="gpt-image-9",
                engine="openai-images",
                cost_usd=0.0,
                metadata={"cost_known": False},
            )
        )
        store.close()
        result = spend(str(tmp_path / "telemetry.db"))
        assert result["total_usd"] == pytest.approx(0.033)
        assert result["images_usd"] == pytest.approx(0.013)
        assert result["image_count"] == 2
        assert result["images_cost_unknown"] == 1


def test_image_bytes_io_has_a_png_name():
    # The SDK infers the upload's type from the file name.
    rig_images = _FakeImages()
    captured = {}

    def edit(**kw):
        captured["name"] = kw["image"].name
        return _response()

    rig_images.edit = edit
    service = ImageService(
        store=None,
        client_factory=lambda: SimpleNamespace(images=rig_images),
        record_telemetry=lambda *a: None,
        settings_loader=lambda: image_settings.ImageSettings(save_dir="unused"),
    )
    service._store = SimpleNamespace(
        save=lambda *a, **k: SimpleNamespace(
            id="img_x",
            path="unused/x.png",
            prompt="p",
            kind="edit",
            model="m",
            quality="q",
            size="s",
            cost_usd=None,
            parent_id=None,
        )
    )
    service.edit(PNG, "x")
    assert captured["name"].endswith(".png")
    assert isinstance(io.BytesIO(PNG), io.BytesIO)

"""Images survive the whole path from request to provider.

Building this found three places that each silently dropped the image, and
every one of them answered "I can't see an image attached" rather than
erroring:

1. ``_to_messages`` never carried ``images`` off the request model.
2. Memory-context injection rebuilds every ChatMessage field by field, so
   anything not named there is lost.
3. ``cloud_router`` has its *own* OpenAI serializer, separate from the engine's
   — cloud models bypass the engine entirely.

Plus a fourth, unrelated to images: the cloud streaming path always sent
``max_tokens``, which reasoning models reject. Nothing had exercised it,
because chat streams through the agent and only an image turn skips the agent.
"""

from __future__ import annotations

from openjarvis.core.types import Message, Role
from openjarvis.engine._base import IMAGE_FORMAT_OPENAI, messages_to_dicts
from openjarvis.server.cloud_router import _is_reasoning_model, _to_openai_msgs
from openjarvis.server.models import ChatMessage
from openjarvis.server.routes import _has_attached_image, _to_messages

B64 = "iVBORw0KGgoAAAANSUhEUg"


def _image_message(content: str = "What is this?") -> Message:
    message = Message(role=Role.USER, content=content)
    message.images = [B64]
    return message


class TestEngineSerializer:
    def test_openai_gets_content_parts(self):
        out = messages_to_dicts([_image_message()], image_format=IMAGE_FORMAT_OPENAI)
        parts = out[0]["content"]
        assert isinstance(parts, list)
        assert parts[0] == {"type": "text", "text": "What is this?"}
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_ollama_keeps_its_sibling_array(self):
        """The default must not change: local vision already worked this way."""
        out = messages_to_dicts([_image_message()])
        assert out[0]["images"] == [B64]
        assert out[0]["content"] == "What is this?"

    def test_a_data_url_is_not_wrapped_twice(self):
        message = Message(role=Role.USER, content="x")
        message.images = ["data:image/jpeg;base64,AAAA"]
        out = messages_to_dicts([message], image_format=IMAGE_FORMAT_OPENAI)
        assert out[0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"

    def test_text_only_messages_are_untouched(self):
        out = messages_to_dicts(
            [Message(role=Role.USER, content="hello")],
            image_format=IMAGE_FORMAT_OPENAI,
        )
        assert out[0]["content"] == "hello"


class TestCloudRouterSerializer:
    """Cloud models bypass the engine, so this serializer needs images too."""

    def test_it_carries_images(self):
        out = _to_openai_msgs([_image_message()])
        parts = out[0]["content"]
        assert isinstance(parts, list)
        assert any(p.get("type") == "image_url" for p in parts)

    def test_text_only_stays_a_plain_string(self):
        out = _to_openai_msgs([Message(role=Role.USER, content="hello")])
        assert out[0]["content"] == "hello"


class TestRequestBoundary:
    def test_images_survive_to_messages(self):
        messages = _to_messages([ChatMessage(role="user", content="x", images=[B64])])
        assert getattr(messages[0], "images", None) == [B64]

    def test_a_text_message_gets_no_images(self):
        messages = _to_messages([ChatMessage(role="user", content="x")])
        assert getattr(messages[0], "images", None) is None

    def test_an_image_turn_is_detected(self):
        class _Req:
            messages = [ChatMessage(role="user", content="x", images=[B64])]

        assert _has_attached_image(_Req()) is True

    def test_a_text_turn_is_not(self):
        class _Req:
            messages = [ChatMessage(role="user", content="x")]

        assert _has_attached_image(_Req()) is False

    def test_only_the_newest_user_turn_counts(self):
        """An image three turns back must not keep routing around the agent."""

        class _Req:
            messages = [
                ChatMessage(role="user", content="old", images=[B64]),
                ChatMessage(role="assistant", content="a red square"),
                ChatMessage(role="user", content="and now?"),
            ]

        assert _has_attached_image(_Req()) is False


class TestReasoningModelTokenParameter:
    """`max_tokens` is rejected outright by reasoning models."""

    def test_luna_is_recognised_as_reasoning(self):
        assert _is_reasoning_model("gpt-5.6-luna") is True

    def test_a_plain_chat_model_is_not(self):
        assert _is_reasoning_model("gpt-4o-mini") is False

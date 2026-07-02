"""Task 10: providers that cannot process images must REJECT image content with a clear
error instead of silently ``str()``-ing it (which would send garbage to the model).

Covers the shared guard (base.message_has_image / reject_image_messages) and both Vertex
providers' ``_convert_messages`` (google_vertex Express + google_genai_vertex).
"""
from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.providers.base import message_has_image, reject_image_messages


def _image_msg():
    return HumanMessage(content=[
        {"type": "text", "text": "Extract fields"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ])


def _text_msgs():
    return [SystemMessage(content="sys"), HumanMessage(content="just text")]


def test_message_has_image_detects_image_block():
    assert message_has_image([_image_msg()]) is True
    assert message_has_image(_text_msgs()) is False
    # list content with no image_url block is NOT an image
    assert message_has_image([HumanMessage(content=[{"type": "text", "text": "x"}])]) is False


def test_reject_image_messages_raises_only_on_images():
    reject_image_messages(_text_msgs(), "google_vertex (Express)")  # text-only -> no raise
    with pytest.raises(ValueError) as ei:
        reject_image_messages([_image_msg()], "google_vertex (Express)")
    assert "image" in str(ei.value).lower()


def test_vertex_express_convert_messages_rejects_images():
    # rejection fires BEFORE the google-genai SDK import inside _convert_messages, so this
    # test does not require the SDK to be installed.
    from app.providers.google_vertex import _VertexExpressChatModel
    m = _VertexExpressChatModel(model="gemini-2.5-flash", api_key="AQ.fake")
    with pytest.raises(ValueError):
        m._convert_messages([_image_msg()])


def test_genai_vertex_convert_messages_rejects_images():
    from app.providers.google_genai_vertex import _GenAIVertexChatModel
    m = _GenAIVertexChatModel(model="gemini-3-pro-preview", api_key="fake")
    with pytest.raises(ValueError):
        m._convert_messages([_image_msg()])

import asyncio
import base64
from io import BytesIO

import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from app.services.prompt_image_editor import (
    EditPlan,
    GeminiPromptImageEditor,
    PromptNotAllowedError,
    _extract_image_bytes,
)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
    return output.getvalue()


class FakeRunnable:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.response


def _editor(plan: EditPlan, generated: bytes) -> GeminiPromptImageEditor:
    editor = GeminiPromptImageEditor.__new__(GeminiPromptImageEditor)
    editor._planner = FakeRunnable(plan)
    editor._editor = FakeRunnable(
        AIMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64.b64encode(generated).decode()}"
                    },
                }
            ]
        )
    )
    editor._timeout_seconds = 1
    editor._max_image_bytes = 10 * 1024 * 1024
    return editor


def test_edit_plans_then_returns_generated_image() -> None:
    generated = _png()
    editor = _editor(
        EditPlan(
            allowed=True,
            instruction="Make the background warmer.",
            preserve_subject=True,
            reason_code="ALLOWED",
        ),
        generated,
    )

    result = asyncio.run(editor.edit(_png(), "배경을 따뜻하게"))

    assert result == generated
    assert len(editor._planner.calls) == 1
    assert len(editor._editor.calls) == 1


def test_edit_rejects_privacy_bypass_before_image_model() -> None:
    editor = _editor(
        EditPlan(
            allowed=False,
            instruction=None,
            preserve_subject=True,
            reason_code="PRIVACY_BYPASS",
        ),
        _png(),
    )

    with pytest.raises(PromptNotAllowedError):
        asyncio.run(editor.edit(_png(), "번호판 블러를 제거해줘"))

    assert editor._editor.calls == []


def test_extract_image_bytes_supports_standard_base64_block() -> None:
    generated = _png()
    assert (
        _extract_image_bytes([{"type": "image", "base64": base64.b64encode(generated).decode()}])
        == generated
    )

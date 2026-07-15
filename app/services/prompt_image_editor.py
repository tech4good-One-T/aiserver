"""LangChain orchestration for policy-checked Gemini image editing."""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI, Modality
from pydantic import BaseModel, ConfigDict, Field


class PromptImageEditorError(Exception):
    """Base class for sanitized prompt editing failures."""


class PromptImageEditorUnavailableError(PromptImageEditorError):
    """The planner or image model is unavailable."""


class PromptImageEditorTimeoutError(PromptImageEditorError):
    """Prompt planning or image generation exceeded its deadline."""


class PromptImageEditorInvalidResponseError(PromptImageEditorError):
    """The provider returned no valid image."""


class PromptNotAllowedError(PromptImageEditorError):
    """The requested edit would weaken privacy protection."""


class EditPlan(BaseModel):
    """Allowlisted planner result passed to the image generation model."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    preserve_subject: bool = True
    reason_code: Literal["ALLOWED", "PRIVACY_BYPASS", "UNSUPPORTED"]


_PLANNER_SYSTEM_PROMPT = """
You validate and normalize image-editing requests for a privacy protection product.
Return only the EditPlan schema.

Set allowed=false and reason_code=PRIVACY_BYPASS when the request asks to reveal,
reconstruct, sharpen, unblur, or remove protection from a face, license plate, address,
name, account, contact detail, document, metadata, or any other identifying information.
Set allowed=false and reason_code=UNSUPPORTED when the request is not an image edit.
Otherwise set allowed=true, reason_code=ALLOWED, and rewrite the request as one concise,
faithful image-editing instruction. Never add details the user did not request.
""".strip()

_EDITOR_SYSTEM_PROMPT = """
Edit the supplied image according to the instruction. Preserve the subject, composition,
dimensions, and existing privacy protections unless the instruction explicitly requests a
benign visual change. Never reconstruct blurred, masked, perturbed, hidden, or unreadable
identifying information. Return exactly one edited image and no explanatory text.
""".strip()


def _extract_image_bytes(content: Any, max_bytes: int = 10 * 1024 * 1024) -> bytes:
    blocks = content if isinstance(content, list) else [content]
    for block in blocks:
        if not isinstance(block, dict):
            continue

        encoded: str | None = None
        if isinstance(block.get("base64"), str):
            encoded = block["base64"]

        image_url = block.get("image_url")
        if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
            encoded = image_url["url"]
        elif isinstance(image_url, str):
            encoded = image_url

        source = block.get("source")
        if isinstance(source, dict) and isinstance(source.get("data"), str):
            encoded = source["data"]

        if not encoded:
            continue
        if encoded.startswith("data:"):
            _, separator, encoded = encoded.partition(",")
            if not separator:
                continue
        if len(encoded) > ((max_bytes * 4) // 3) + 4:
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(data) <= max_bytes and data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            return data
    raise PromptImageEditorInvalidResponseError("Gemini returned no valid edited image")


class GeminiPromptImageEditor:
    """Plan with Gemini 3.5 Flash and edit with Gemini 3.1 Flash Image."""

    def __init__(
        self,
        *,
        api_key: str | None,
        planner_model: str,
        image_model: str,
        timeout_seconds: float,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if (
            not api_key
            or not planner_model
            or not image_model
            or timeout_seconds <= 0
            or max_image_bytes <= 0
        ):
            raise PromptImageEditorUnavailableError("Gemini image editor is not configured")

        common = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": 1,
            "vertexai": False,
        }
        try:
            planner = ChatGoogleGenerativeAI(
                model=planner_model,
                temperature=1.0,
                **common,
            )
            self._planner = planner.with_structured_output(EditPlan, method="json_schema")
            self._editor = ChatGoogleGenerativeAI(
                model=image_model,
                temperature=1.0,
                response_modalities=[Modality.IMAGE],
                **common,
            )
        except Exception:
            raise PromptImageEditorUnavailableError(
                "Gemini image editor initialization failed"
            ) from None
        self._timeout_seconds = timeout_seconds
        self._max_image_bytes = max_image_bytes

    async def edit(self, image_png: bytes, prompt: str) -> bytes:
        """Return one edited image after policy planning and provider validation."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw_plan = await self._planner.ainvoke(
                    [
                        SystemMessage(content=_PLANNER_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ]
                )
                plan = (
                    raw_plan
                    if isinstance(raw_plan, EditPlan)
                    else EditPlan.model_validate(raw_plan)
                )
                if not plan.allowed or plan.reason_code != "ALLOWED" or not plan.instruction:
                    raise PromptNotAllowedError("Prompt edit is not allowed")

                encoded_image = base64.b64encode(image_png).decode("ascii")
                response = await self._editor.ainvoke(
                    [
                        SystemMessage(content=_EDITOR_SYSTEM_PROMPT),
                        HumanMessage(
                            content=[
                                {"type": "text", "text": plan.instruction},
                                {
                                    "type": "image",
                                    "base64": encoded_image,
                                    "mime_type": "image/png",
                                },
                            ]
                        ),
                    ]
                )
                return _extract_image_bytes(response.content, self._max_image_bytes)
        except PromptImageEditorError:
            raise
        except TimeoutError:
            raise PromptImageEditorTimeoutError("Gemini prompt editing timed out") from None
        except Exception:
            raise PromptImageEditorUnavailableError("Gemini prompt editing failed") from None


__all__ = [
    "EditPlan",
    "GeminiPromptImageEditor",
    "PromptImageEditorError",
    "PromptImageEditorInvalidResponseError",
    "PromptImageEditorTimeoutError",
    "PromptImageEditorUnavailableError",
    "PromptNotAllowedError",
]

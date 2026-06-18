"""Job payload validation.

Mirrors the Zod schemas in ``node.ts`` / the broker dispatch contract in
``node-broker-service/src/index.ts``. The wire format uses camelCase keys; these
dataclasses use snake_case and the parsers do the mapping. Validation failures
raise :class:`PayloadError` so the caller can report ``invalid_payload``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_SOURCES = ("checkpoint", "diffusion_model")
CLIP_TYPES = ("sdxl", "sd3", "flux")


class PayloadError(ValueError):
    """Raised when an incoming job payload fails validation."""


@dataclass(frozen=True)
class WorkflowPatchInput:
    node_id: str
    input_key: str
    value: Any


@dataclass(frozen=True)
class ImageGenerationPayload:
    kind: str
    prompt: str
    width: int
    height: int
    checkpoint: str
    model_source: str = "checkpoint"
    clip_type: str | None = None
    text_encoder1: str | None = None
    text_encoder2: str | None = None
    vae_name: str | None = None
    negative_prompt: str | None = None
    seed: int | None = None
    steps: int | None = None
    workflow_file: str | None = None
    workflow_patches: tuple[WorkflowPatchInput, ...] = ()


@dataclass(frozen=True)
class AiCaptionInput:
    image_data: str
    image_content_type: str
    model: str
    quantization: str
    prompt_style: str
    caption_length: str
    memory_management: str
    filename: str | None = None


@dataclass(frozen=True)
class AiCaptionPayload:
    kind: str
    app_slug: str
    input: AiCaptionInput


@dataclass(frozen=True)
class UpscaleInput:
    image_data: str
    image_content_type: str
    upscale_model: str
    filename: str | None = None


@dataclass(frozen=True)
class UpscaleAppPayload:
    kind: str
    app_slug: str
    input: UpscaleInput


JobPayload = ImageGenerationPayload | AiCaptionPayload | UpscaleAppPayload


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"`{key}` must be a non-empty string")
    return value


def _opt_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PayloadError(f"`{key}` must be a string")
    trimmed = value.strip()
    return trimmed or None


def _require_pos_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PayloadError(f"`{key}` must be a positive integer")
    return value


def _parse_image_generation(data: dict[str, Any]) -> ImageGenerationPayload:
    model_source = data.get("modelSource") or "checkpoint"
    if model_source not in MODEL_SOURCES:
        raise PayloadError(f"`modelSource` must be one of {MODEL_SOURCES}")

    clip_type = data.get("clipType")
    if clip_type is not None and clip_type not in CLIP_TYPES:
        raise PayloadError(f"`clipType` must be one of {CLIP_TYPES}")

    seed = data.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
        raise PayloadError("`seed` must be a non-negative integer")

    steps = data.get("steps")
    if steps is not None and (
        not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 150
    ):
        raise PayloadError("`steps` must be an integer in 1..150")

    raw_patches = data.get("workflowPatches") or []
    patches: list[WorkflowPatchInput] = []
    for entry in raw_patches:
        if not isinstance(entry, dict):
            raise PayloadError("`workflowPatches` entries must be objects")
        patches.append(
            WorkflowPatchInput(
                node_id=_require_str(entry, "nodeId"),
                input_key=_require_str(entry, "inputKey"),
                value=entry.get("value"),
            )
        )

    return ImageGenerationPayload(
        kind="image_generation",
        prompt=_require_str(data, "prompt"),
        width=_require_pos_int(data, "width"),
        height=_require_pos_int(data, "height"),
        checkpoint=_require_str(data, "checkpoint"),
        model_source=model_source,
        clip_type=clip_type,
        text_encoder1=_opt_str(data, "textEncoder1"),
        text_encoder2=_opt_str(data, "textEncoder2"),
        vae_name=_opt_str(data, "vaeName"),
        negative_prompt=_opt_str(data, "negativePrompt"),
        seed=seed,
        steps=steps,
        workflow_file=_opt_str(data, "workflowFile"),
        workflow_patches=tuple(patches),
    )


def _parse_ai_caption(data: dict[str, Any]) -> AiCaptionPayload:
    if data.get("appSlug") != "ai-caption":
        raise PayloadError("unsupported appSlug")
    raw_input = data.get("input")
    if not isinstance(raw_input, dict):
        raise PayloadError("`input` must be an object")

    return AiCaptionPayload(
        kind="app_run",
        app_slug="ai-caption",
        input=AiCaptionInput(
            image_data=_require_str(raw_input, "imageData"),
            image_content_type=_require_str(raw_input, "imageContentType"),
            filename=_opt_str(raw_input, "filename"),
            model=_require_str(raw_input, "model"),
            quantization=_require_str(raw_input, "quantization"),
            prompt_style=_require_str(raw_input, "promptStyle"),
            caption_length=_require_str(raw_input, "captionLength"),
            memory_management=_require_str(raw_input, "memoryManagement"),
        ),
    )


def _parse_upscale(data: dict[str, Any]) -> UpscaleAppPayload:
    raw_input = data.get("input")
    if not isinstance(raw_input, dict):
        raise PayloadError("`input` must be an object")

    return UpscaleAppPayload(
        kind="app_run",
        app_slug="upscale-image",
        input=UpscaleInput(
            image_data=_require_str(raw_input, "imageData"),
            image_content_type=_require_str(raw_input, "imageContentType"),
            filename=_opt_str(raw_input, "filename"),
            upscale_model=_require_str(raw_input, "upscaleModel"),
        ),
    )


# Dispatch an ``app_run`` payload by its ``appSlug``. Each app has its own input
# contract, so the slug — not just ``kind`` — selects the parser.
_APP_RUN_PARSERS = {
    "ai-caption": _parse_ai_caption,
    "upscale-image": _parse_upscale,
}


def parse_job_payload(data: Any) -> JobPayload:
    """Validate a raw job payload dict into a typed payload, by ``kind``."""
    if not isinstance(data, dict):
        raise PayloadError("payload must be an object")
    kind = data.get("kind")
    if kind == "image_generation":
        return _parse_image_generation(data)
    if kind == "app_run":
        parser = _APP_RUN_PARSERS.get(data.get("appSlug"))
        if parser is None:
            raise PayloadError("unsupported appSlug")
        return parser(data)
    raise PayloadError(f"unsupported job kind: {kind!r}")

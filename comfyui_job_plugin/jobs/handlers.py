"""Job execution. Ports ``generateImage`` / ``runAiCaption`` / ``executeJob``
from ``node.ts``, driving the ComfyUI HTTP client and the workflow builders."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..comfy_api import ComfyApiClient, ComfyApiError
from ..config import PluginConfig
from ..logging_util import log
from ..workflows.caption import extract_caption_from_outputs, sanitize_filename
from ..workflows.image import build_image_workflow
from ..workflows.loader import WorkflowPatch, load_workflow, patch_workflow
from .schemas import AiCaptionPayload, ImageGenerationPayload, JobPayload

# Returns True when the running job should stop polling (abandoned / superseded).
AbortCheck = Callable[[], bool]


@dataclass(frozen=True)
class JobSuccess:
    content_type: str
    data: str  # base64
    seed: int | None = None


@dataclass(frozen=True)
class JobFailure:
    error: str
    details: Any = None


JobResult = JobSuccess | JobFailure


def generate_image(
    payload: ImageGenerationPayload,
    timeout_ms: int,
    comfy: ComfyApiClient,
    config: PluginConfig,
    should_abort: AbortCheck | None = None,
) -> JobResult:
    try:
        installed = comfy.fetch_available_models() or []
        built = build_image_workflow(payload, installed, config.negative_prompt)

        prompt_id = comfy.submit_workflow(built.workflow, "generate")
        try:
            outputs = comfy.wait_for_outputs(prompt_id, timeout_ms, should_abort)
        finally:
            comfy.delete_history(prompt_id)

        for output in outputs.values():
            images = output.get("images") if isinstance(output, dict) else None
            if not images:
                continue
            meta = images[0]
            try:
                data, content_type = comfy.view_image(
                    meta["filename"], meta.get("subfolder", ""), meta.get("type", "")
                )
            except Exception as error:  # noqa: BLE001 - try the next output, like the TS loop
                log("Failed to download ComfyUI image", error)
                continue
            return JobSuccess(
                content_type=content_type,
                data=base64.b64encode(data).decode("ascii"),
                seed=built.seed,
            )

        return JobFailure("invalid_result", "ComfyUI did not produce an image output")
    except ComfyApiError as error:
        return JobFailure(error.code, error.details)
    except Exception as error:  # noqa: BLE001 - mirror the TS catch-all
        return JobFailure("internal_error", str(error))


def run_ai_caption(
    payload: AiCaptionPayload,
    timeout_ms: int,
    comfy: ComfyApiClient,
    config: PluginConfig,
    should_abort: AbortCheck | None = None,
) -> JobResult:
    job_input = payload.input
    try:
        try:
            image_bytes = base64.b64decode(job_input.image_data, validate=True)
        except (binascii.Error, ValueError):
            return JobFailure("invalid_payload", "Failed to decode uploaded image")
        if not image_bytes:
            return JobFailure("invalid_payload", "Uploaded image was empty")

        filename = sanitize_filename(job_input.filename, job_input.image_content_type)
        uploaded_name = comfy.upload_image(
            image_bytes, job_input.image_content_type, filename
        )

        workflow = load_workflow("ai-caption")
        patched = patch_workflow(
            workflow,
            [
                WorkflowPatch("1", "image", uploaded_name),
                WorkflowPatch("2", "model", job_input.model),
                WorkflowPatch("2", "quantization", job_input.quantization),
                WorkflowPatch("2", "prompt_style", job_input.prompt_style),
                WorkflowPatch("2", "caption_length", job_input.caption_length),
                WorkflowPatch("2", "memory_management", job_input.memory_management),
            ],
        )

        prompt_id = comfy.submit_workflow(patched, "ai-caption")
        try:
            outputs = comfy.wait_for_outputs(prompt_id, timeout_ms, should_abort)
        finally:
            comfy.delete_history(prompt_id)

        caption = extract_caption_from_outputs(outputs)
        if not caption:
            return JobFailure(
                "invalid_result",
                "ComfyUI completed the workflow but no caption text was found",
            )

        data = base64.b64encode(json.dumps({"caption": caption}).encode("utf8")).decode("ascii")
        return JobSuccess(content_type="application/json", data=data)
    except ComfyApiError as error:
        return JobFailure(error.code, error.details)
    except Exception as error:  # noqa: BLE001 - mirror the TS catch-all
        return JobFailure("internal_error", str(error))


def execute_job(
    payload: JobPayload,
    timeout_ms: int,
    comfy: ComfyApiClient,
    config: PluginConfig,
    should_abort: AbortCheck | None = None,
) -> JobResult:
    if isinstance(payload, ImageGenerationPayload):
        return generate_image(payload, timeout_ms, comfy, config, should_abort)
    if isinstance(payload, AiCaptionPayload):
        return run_ai_caption(payload, timeout_ms, comfy, config, should_abort)
    return JobFailure("invalid_result", "Unsupported job payload")

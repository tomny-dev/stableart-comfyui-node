"""Image-generation workflow builder. Ports ``buildImageWorkflow`` from ``node.ts``.

Pure: the caller fetches the installed model list and passes it in, so this is
fully unit-testable without ComfyUI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..jobs.schemas import ImageGenerationPayload
from .checkpoints import resolve_checkpoint
from .loader import WorkflowGraph, WorkflowPatch, load_workflow, patch_workflow

DEFAULT_STEPS = 20
DEFAULT_CLIP_NAME1 = "clip_l.safetensors"
DEFAULT_CLIP_NAME2 = "t5/t5xxl_fp16.safetensors"
DEFAULT_CLIP_TYPE = "sdxl"
DEFAULT_VAE_NAME = "ae.safetensors"


@dataclass(frozen=True)
class BuiltWorkflow:
    workflow: WorkflowGraph
    seed: int


def build_image_workflow(
    opts: ImageGenerationPayload,
    installed_models: list[str],
    default_negative: str,
) -> BuiltWorkflow:
    seed = opts.seed if opts.seed is not None else random.randint(0, 999_999)
    steps = opts.steps if opts.steps is not None else DEFAULT_STEPS
    negative = opts.negative_prompt or default_negative

    if opts.workflow_file:
        workflow = load_workflow(opts.workflow_file)
        patches = [
            WorkflowPatch("3", "seed", seed),
            WorkflowPatch("3", "steps", steps),
            *(
                WorkflowPatch(p.node_id, p.input_key, p.value)
                for p in opts.workflow_patches
            ),
        ]
        return BuiltWorkflow(patch_workflow(workflow, patches), seed)

    if opts.model_source == "diffusion_model":
        resolved_model = resolve_checkpoint(opts.checkpoint, installed_models)
        workflow = load_workflow("checkpoint-image-diffusion")
        patches = [
            WorkflowPatch("3", "seed", seed),
            WorkflowPatch("3", "steps", steps),
            WorkflowPatch("4", "unet_name", resolved_model),
            WorkflowPatch("5", "width", opts.width),
            WorkflowPatch("5", "height", opts.height),
            WorkflowPatch("6", "text", opts.prompt),
            WorkflowPatch("7", "text", negative),
            WorkflowPatch("10", "clip_name1", opts.text_encoder1 or DEFAULT_CLIP_NAME1),
            WorkflowPatch("10", "clip_name2", opts.text_encoder2 or DEFAULT_CLIP_NAME2),
            WorkflowPatch("10", "type", opts.clip_type or DEFAULT_CLIP_TYPE),
            WorkflowPatch("11", "vae_name", opts.vae_name or DEFAULT_VAE_NAME),
        ]
        return BuiltWorkflow(patch_workflow(workflow, patches), seed)

    resolved_checkpoint = resolve_checkpoint(opts.checkpoint, installed_models)
    workflow = load_workflow("checkpoint-image")
    patches = [
        WorkflowPatch("3", "seed", seed),
        WorkflowPatch("3", "steps", steps),
        WorkflowPatch("4", "ckpt_name", resolved_checkpoint),
        WorkflowPatch("5", "width", opts.width),
        WorkflowPatch("5", "height", opts.height),
        WorkflowPatch("6", "text", opts.prompt),
        WorkflowPatch("7", "text", negative),
    ]
    return BuiltWorkflow(patch_workflow(workflow, patches), seed)

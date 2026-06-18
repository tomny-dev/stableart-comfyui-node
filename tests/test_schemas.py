import pytest

from comfyui_job_plugin.jobs.schemas import (
    AiCaptionPayload,
    ImageGenerationPayload,
    PayloadError,
    UpscaleAppPayload,
    parse_job_payload,
)


def test_parse_image_generation_minimal_defaults():
    payload = parse_job_payload(
        {
            "kind": "image_generation",
            "prompt": "hi",
            "width": 512,
            "height": 512,
            "checkpoint": "x.safetensors",
        }
    )
    assert isinstance(payload, ImageGenerationPayload)
    assert payload.model_source == "checkpoint"  # default applied
    assert payload.seed is None
    assert payload.workflow_patches == ()


def test_parse_image_generation_full():
    payload = parse_job_payload(
        {
            "kind": "image_generation",
            "prompt": "hi",
            "width": 1024,
            "height": 1024,
            "checkpoint": "x",
            "modelSource": "diffusion_model",
            "clipType": "flux",
            "textEncoder1": "clip.safetensors",
            "seed": 7,
            "steps": 25,
            "workflowPatches": [{"nodeId": "3", "inputKey": "cfg", "value": 4}],
        }
    )
    assert payload.model_source == "diffusion_model"
    assert payload.clip_type == "flux"
    assert payload.text_encoder1 == "clip.safetensors"
    assert payload.seed == 7
    assert payload.steps == 25
    assert payload.workflow_patches[0].node_id == "3"


def test_parse_rejects_missing_required_field():
    with pytest.raises(PayloadError):
        parse_job_payload({"kind": "image_generation", "prompt": "hi", "width": 512})


def test_parse_rejects_bad_enum():
    with pytest.raises(PayloadError):
        parse_job_payload(
            {
                "kind": "image_generation",
                "prompt": "hi",
                "width": 512,
                "height": 512,
                "checkpoint": "x",
                "modelSource": "nonsense",
            }
        )


def test_parse_rejects_out_of_range_steps():
    with pytest.raises(PayloadError):
        parse_job_payload(
            {
                "kind": "image_generation",
                "prompt": "hi",
                "width": 512,
                "height": 512,
                "checkpoint": "x",
                "steps": 999,
            }
        )


def test_parse_ai_caption():
    payload = parse_job_payload(
        {
            "kind": "app_run",
            "appSlug": "ai-caption",
            "input": {
                "imageData": "Zm9v",
                "imageContentType": "image/png",
                "model": "joy",
                "quantization": "fp16",
                "promptStyle": "descriptive",
                "captionLength": "long",
                "memoryManagement": "balanced",
            },
        }
    )
    assert isinstance(payload, AiCaptionPayload)
    assert payload.input.model == "joy"
    assert payload.input.prompt_style == "descriptive"


def test_parse_upscale():
    payload = parse_job_payload(
        {
            "kind": "app_run",
            "appSlug": "upscale-image",
            "input": {
                "imageData": "Zm9v",
                "imageContentType": "image/png",
                "filename": "pic.png",
                "upscaleModel": "4x-UltraSharp.pth",
            },
        }
    )
    assert isinstance(payload, UpscaleAppPayload)
    assert payload.input.upscale_model == "4x-UltraSharp.pth"
    assert payload.input.filename == "pic.png"


def test_parse_upscale_rejects_missing_model():
    with pytest.raises(PayloadError):
        parse_job_payload(
            {
                "kind": "app_run",
                "appSlug": "upscale-image",
                "input": {
                    "imageData": "Zm9v",
                    "imageContentType": "image/png",
                },
            }
        )


def test_parse_rejects_unknown_kind():
    with pytest.raises(PayloadError):
        parse_job_payload({"kind": "video_generation"})


def test_parse_rejects_unknown_app_slug():
    with pytest.raises(PayloadError):
        parse_job_payload({"kind": "app_run", "appSlug": "other", "input": {}})

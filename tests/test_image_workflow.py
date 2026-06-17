from comfyui_job_plugin.jobs.schemas import ImageGenerationPayload
from comfyui_job_plugin.workflows.image import DEFAULT_STEPS, build_image_workflow


def _payload(**overrides):
    base = dict(
        kind="image_generation",
        prompt="a red fox",
        width=768,
        height=512,
        checkpoint="anime_xl.safetensors",
    )
    base.update(overrides)
    return ImageGenerationPayload(**base)


def test_checkpoint_workflow_patches():
    built = build_image_workflow(
        _payload(seed=42, steps=30, negative_prompt="bad neg"),
        ["anime_xl.safetensors"],
    )
    wf = built.workflow
    assert built.seed == 42
    assert wf["3"]["inputs"]["seed"] == 42
    assert wf["3"]["inputs"]["steps"] == 30
    assert wf["4"]["inputs"]["ckpt_name"] == "anime_xl.safetensors"
    assert wf["5"]["inputs"]["width"] == 768
    assert wf["5"]["inputs"]["height"] == 512
    assert wf["6"]["inputs"]["text"] == "a red fox"
    assert wf["7"]["inputs"]["text"] == "bad neg"


def test_defaults_steps_and_random_seed_and_empty_negative():
    built = build_image_workflow(_payload(), [])
    assert built.workflow["3"]["inputs"]["steps"] == DEFAULT_STEPS
    assert 0 <= built.seed <= 999_999
    # No negative provided and the node carries no default -> empty negative.
    assert built.workflow["7"]["inputs"]["text"] == ""


def test_explicit_negative_is_used():
    built = build_image_workflow(_payload(negative_prompt="ugly"), [])
    assert built.workflow["7"]["inputs"]["text"] == "ugly"


def test_diffusion_model_workflow_uses_separate_loaders_and_defaults():
    built = build_image_workflow(
        _payload(model_source="diffusion_model", checkpoint="flux_dev"),
        ["flux_dev.safetensors"],
    )
    wf = built.workflow
    assert wf["4"]["class_type"] == "UNETLoader"
    assert wf["4"]["inputs"]["unet_name"] == "flux_dev.safetensors"
    assert wf["10"]["inputs"]["clip_name1"] == "clip_l.safetensors"
    assert wf["10"]["inputs"]["clip_name2"] == "t5/t5xxl_fp16.safetensors"
    assert wf["10"]["inputs"]["type"] == "sdxl"
    assert wf["11"]["inputs"]["vae_name"] == "ae.safetensors"


def test_diffusion_model_honours_explicit_encoders():
    built = build_image_workflow(
        _payload(
            model_source="diffusion_model",
            checkpoint="flux_dev.safetensors",
            text_encoder1="clip_g.safetensors",
            text_encoder2="t5xxl.safetensors",
            clip_type="flux",
            vae_name="custom_ae.safetensors",
        ),
        ["flux_dev.safetensors"],
    )
    wf = built.workflow
    assert wf["10"]["inputs"]["clip_name1"] == "clip_g.safetensors"
    assert wf["10"]["inputs"]["clip_name2"] == "t5xxl.safetensors"
    assert wf["10"]["inputs"]["type"] == "flux"
    assert wf["11"]["inputs"]["vae_name"] == "custom_ae.safetensors"


def test_builder_does_not_mutate_bundled_graph():
    # Two builds must be independent — the loader deep-copies the bundled JSON.
    first = build_image_workflow(_payload(seed=1), ["anime_xl.safetensors"])
    second = build_image_workflow(_payload(seed=2), ["anime_xl.safetensors"])
    assert first.workflow["3"]["inputs"]["seed"] == 1
    assert second.workflow["3"]["inputs"]["seed"] == 2

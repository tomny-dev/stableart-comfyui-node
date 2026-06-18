import pytest

from comfyui_job_plugin.workflows.loader import (
    WorkflowPatch,
    load_workflow,
    patch_workflow,
)


def test_load_workflow_reads_bundled_graph():
    wf = load_workflow("checkpoint-image")
    assert wf["4"]["class_type"] == "CheckpointLoaderSimple"


def test_upscale_workflow_loads_and_patches():
    wf = load_workflow("upscale-image")
    assert wf["2"]["class_type"] == "UpscaleModelLoader"
    assert wf["3"]["class_type"] == "ImageUpscaleWithModel"
    patched = patch_workflow(
        wf,
        [
            WorkflowPatch("1", "image", "uploaded.png"),
            WorkflowPatch("2", "model_name", "4x-UltraSharp.pth"),
        ],
    )
    assert patched["1"]["inputs"]["image"] == "uploaded.png"
    assert patched["2"]["inputs"]["model_name"] == "4x-UltraSharp.pth"


@pytest.mark.parametrize(
    "bad",
    ["../data/checkpoint-image", "..\\data\\checkpoint-image", "a/b", "x.json", ""],
)
def test_load_workflow_rejects_unsafe_name(bad):
    # Names must be plain [A-Za-z0-9_-]; any path separator is rejected (a
    # backslash is not a separator on POSIX, so basename alone was insufficient).
    with pytest.raises(ValueError):
        load_workflow(bad)


def test_patch_workflow_clones_and_applies():
    original = {"1": {"class_type": "X", "inputs": {"a": 1}}}
    patched = patch_workflow(original, [WorkflowPatch("1", "a", 99)])
    assert patched["1"]["inputs"]["a"] == 99
    assert original["1"]["inputs"]["a"] == 1  # original untouched (deep copy)


def test_patch_workflow_raises_on_missing_node():
    with pytest.raises(ValueError, match="not found"):
        patch_workflow({"1": {"class_type": "X", "inputs": {}}}, [WorkflowPatch("99", "a", 1)])

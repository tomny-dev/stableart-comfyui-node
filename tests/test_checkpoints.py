from comfyui_job_plugin.workflows.checkpoints import resolve_checkpoint

INSTALLED = ["sdxl/realvis_v4.safetensors", "anime_xl.safetensors", "flux_dev.safetensors"]


def test_exact_match_wins():
    assert resolve_checkpoint("anime_xl.safetensors", INSTALLED) == "anime_xl.safetensors"


def test_basename_match_strips_dir_and_ext():
    assert resolve_checkpoint("realvis_v4", INSTALLED) == "sdxl/realvis_v4.safetensors"


def test_prefix_match():
    assert resolve_checkpoint("flux", INSTALLED) == "flux_dev.safetensors"


def test_fallback_returns_original_when_unmatched():
    assert resolve_checkpoint("does_not_exist", INSTALLED) == "does_not_exist"


def test_empty_request_does_not_fuzzy_match_first_model():
    # An empty needle must not prefix-match (startswith("")) the first installed.
    assert resolve_checkpoint("", INSTALLED) == ""

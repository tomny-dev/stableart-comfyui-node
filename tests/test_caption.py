from comfyui_job_plugin.workflows.caption import (
    extension_for_content_type,
    extract_caption_from_outputs,
    extract_first_non_empty_string,
    sanitize_filename,
)


def test_extract_first_non_empty_prefers_keys():
    value = {"misc": "", "result": {"text": "the caption"}}
    assert extract_first_non_empty_string(value) == "the caption"


def test_extract_first_non_empty_recurses_lists():
    assert extract_first_non_empty_string(["", "  ", ["found"]]) == "found"


def test_extract_first_non_empty_returns_none_when_blank():
    assert extract_first_non_empty_string({"a": "", "b": ["   "]}) is None


def test_extract_caption_prefers_node_5_then_2():
    outputs = {"2": {"text": ["node2"]}, "5": {"text": ["node5"]}}
    assert extract_caption_from_outputs(outputs) == "node5"


def test_extract_caption_falls_back_to_any_node():
    outputs = {"9": {"text": ["only node"]}}
    assert extract_caption_from_outputs(outputs) == "only node"


def test_extension_for_content_type():
    assert extension_for_content_type("image/jpeg") == ".jpg"
    assert extension_for_content_type("image/webp") == ".webp"
    assert extension_for_content_type("image/gif") == ".gif"
    assert extension_for_content_type("image/png") == ".png"
    assert extension_for_content_type("application/octet-stream") == ".png"


def test_sanitize_filename_strips_unsafe_chars():
    assert sanitize_filename("my photo!.png", "image/png") == "my-photo-.png"


def test_sanitize_filename_fallback_when_empty():
    name = sanitize_filename("   ", "image/jpeg")
    assert name.startswith("stableart-caption-")
    assert name.endswith(".jpg")


def test_sanitize_filename_uses_basename():
    assert sanitize_filename("/etc/passwd", "image/png") == "passwd"


def test_sanitize_filename_strips_windows_path():
    # A Windows-client path must reduce to its basename on a POSIX node too.
    assert sanitize_filename(r"C:\Users\Name\Pictures\photo.png", "image/png") == (
        "photo.png"
    )


def test_sanitize_filename_rejects_dot_segments():
    for bad in (".", "..", "/a/.", "x/.."):
        assert sanitize_filename(bad, "image/png").startswith("stableart-caption-")

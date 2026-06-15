"""ai-caption output extraction and filename helpers. Ports the corresponding
helpers from ``node.ts`` (``extractCaptionFromOutputs``, ``sanitizeFilename``)."""

from __future__ import annotations

import os
import re
import time
from typing import Any

_PREFERRED_KEYS = ("text", "string", "caption", "captions", "result")
_PREFERRED_NODE_IDS = ("5", "2")
_FILENAME_SANITIZE = re.compile(r"[^\w.\-]+")


def extension_for_content_type(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type.lower(), ".png")


def extract_first_non_empty_string(value: Any) -> str | None:
    """Recursively find the first non-empty string, preferring caption-ish keys."""
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None

    if isinstance(value, list):
        for entry in value:
            found = extract_first_non_empty_string(entry)
            if found:
                return found
        return None

    if isinstance(value, dict):
        for key in _PREFERRED_KEYS:
            if key in value:
                found = extract_first_non_empty_string(value[key])
                if found:
                    return found
        for nested in value.values():
            found = extract_first_non_empty_string(nested)
            if found:
                return found

    return None


def extract_caption_from_outputs(outputs: dict[str, Any]) -> str | None:
    """Pull caption text from a ComfyUI ``outputs`` map, preferring nodes 5/2."""
    for node_id in _PREFERRED_NODE_IDS:
        found = extract_first_non_empty_string(outputs.get(node_id))
        if found:
            return found
    for node_output in outputs.values():
        found = extract_first_non_empty_string(node_output)
        if found:
            return found
    return None


def sanitize_filename(filename: str | None, content_type: str) -> str:
    """Sanitize an upload filename, falling back to a generated one."""
    fallback = f"stableart-caption-{time.time_ns()}{extension_for_content_type(content_type)}"
    if not filename or not filename.strip():
        return fallback
    # Normalize Windows separators first (a backslash isn't a separator on POSIX),
    # so a path from a Windows client is reduced to its basename, matching
    # checkpoints._basename.
    basename = _FILENAME_SANITIZE.sub(
        "-", os.path.basename(filename.strip().replace("\\", "/"))
    )
    # "." / ".." survive basename + the sanitize regex (dots are allowed); never
    # forward them as a filename to the upload endpoint.
    if basename in (".", ".."):
        return fallback
    return basename or fallback

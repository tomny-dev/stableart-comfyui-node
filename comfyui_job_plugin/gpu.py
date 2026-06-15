"""GPU name detection. Ports ``resolveGpuName`` from ``node.ts``."""

from __future__ import annotations

import subprocess

_FALLBACK = "Unknown GPU"


def resolve_gpu_name(override: str | None = None) -> str:
    """Return the GPU label: explicit override, else ``nvidia-smi``, else fallback."""
    if override and override.strip():
        return override.strip()

    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _FALLBACK

    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return _FALLBACK

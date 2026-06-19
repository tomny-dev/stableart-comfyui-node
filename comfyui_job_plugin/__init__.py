"""StableArt ComfyUI job plugin.

A faithful Python port of ``apps/docker-job-node/src/node.ts`` that runs inside
ComfyUI as a custom node instead of a separate Docker process (ADR-0003). It
connects to the StableArt node broker over WebSocket and executes
image-generation and ai-caption jobs against the local ComfyUI HTTP API.
"""

from comfyui_job_plugin import runtime
from comfyui_job_plugin.runtime import PROTOCOL_VERSION
from comfyui_job_plugin.version import __version__

__all__ = ["runtime", "PROTOCOL_VERSION", "__version__"]

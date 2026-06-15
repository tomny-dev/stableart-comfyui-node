"""ComfyUI entrypoint for the StableArt job plugin.

ComfyUI imports this module synchronously while it starts up, *before* its own
HTTP server is listening. So this file must return fast: it only registers the
(empty) node mappings ComfyUI expects and kicks off the broker client on a
background daemon thread. All blocking work — waiting for the local server,
connecting to the broker, running jobs — happens in that thread, never here.
"""

import os
import sys
from pathlib import Path

# ComfyUI loads a custom node by path and does NOT put the node's own directory on
# sys.path, so the bundled `comfyui_job_plugin` package isn't importable as a
# top-level module (and neither are its importlib.resources data lookups). Add this
# directory to sys.path so the package — designed and tested as a top-level
# `comfyui_job_plugin` — resolves whether installed via ComfyUI-Manager or pip.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comfyui_job_plugin import runtime  # noqa: E402  (import after sys.path setup)

# This plugin contributes no graph nodes; it is a background broker client. ComfyUI
# still expects these names to exist on a custom node module.
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

# Serve the bundled web extension (the Settings-panel config UI). ComfyUI loads
# every .js under this directory into the front end.
WEB_DIRECTORY = "./web"

# Start the broker client. Idempotent and non-blocking; degrades to a no-op (with
# a logged warning) if the broker URL / API key are not configured.
runtime.start(plugin_dir=Path(__file__).parent)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

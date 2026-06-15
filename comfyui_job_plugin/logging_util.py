"""Logging for the plugin.

Uses the stdlib ``logging`` module so output integrates with ComfyUI's logging
configuration (levels, formatting, the server log file) instead of bypassing it
with bare prints. ``log()`` keeps the variadic call style used throughout the
port (a thin shim over ``logger.info``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("comfyui-job-plugin")


def log(*args: object) -> None:
    """Emit an info-level log line, joining args like the old print did."""
    logger.info(" ".join(str(arg) for arg in args))

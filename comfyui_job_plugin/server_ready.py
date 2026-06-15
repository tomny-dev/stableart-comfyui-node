"""Discover the local ComfyUI HTTP base URL and wait until it is listening.

The plugin runs inside ComfyUI, but ComfyUI imports custom nodes *before* its own
server is up. The broker client therefore resolves the base URL and polls it from
its background thread before issuing any HTTP calls.
"""

from __future__ import annotations

import threading

import requests

from .logging_util import log

_DEFAULT_BASE_URL = "http://127.0.0.1:8188"


def discover_base_url() -> str:
    """Best-effort resolution of the **local** ComfyUI base URL.

    The node always drives the ComfyUI it runs inside: ComfyUI's own
    ``PromptServer`` bound port, then the ``:8188`` default. Operators run
    ComfyUI on ``--port``, so we never assume the default when we can ask.
    """
    try:  # available only when imported inside a running ComfyUI
        from server import PromptServer  # type: ignore[import-not-found]

        instance = getattr(PromptServer, "instance", None)
        port = getattr(instance, "port", None)
        if port:
            return f"http://127.0.0.1:{port}"
    except Exception:  # noqa: BLE001 - import/attr shape varies across ComfyUI versions
        pass

    return _DEFAULT_BASE_URL


def await_server_ready(
    stop: threading.Event,
    *,
    poll_seconds: float = 1.0,
) -> str | None:
    """Block until the local ComfyUI HTTP server answers, returning its base URL.

    The base URL is resolved **inside** the loop on every attempt: ComfyUI imports
    custom nodes before ``PromptServer`` binds its port, so a one-shot resolution at
    import time would latch onto the ``:8188`` default and never notice a ``--port``
    server. Re-resolving each tick picks up the real port as soon as it is bound.

    Returns the discovered base URL once ``GET /system_stats`` responds, or None if
    ``stop`` is set first. Runs in the broker background thread, so blocking is fine.
    """
    announced = False
    while not stop.is_set():
        base_url = discover_base_url()
        try:
            response = requests.get(f"{base_url}/system_stats", timeout=5)
            if response.ok:
                return base_url
        except requests.RequestException:
            pass
        if not announced:
            log(f"Waiting for local ComfyUI (checking {base_url}) ...")
            announced = True
        stop.wait(poll_seconds)
    return None

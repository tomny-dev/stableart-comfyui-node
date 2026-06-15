"""Process-wide lifecycle for the broker client.

``start()`` is called from the ComfyUI entrypoint (the top-level ``__init__.py``).
It is idempotent and non-blocking: it loads config, and if the plugin is
configured it spawns the broker client on a daemon thread. A misconfigured or
unconfigured plugin logs a warning and stays idle — it never raises, so it can't
break ComfyUI startup.
"""

from __future__ import annotations

import atexit
import threading
from pathlib import Path

from .broker_client import BrokerClient
from .config import load_config
from .logging_util import log

# Handshake protocol version this client speaks. v2 adds operator-managed model
# install/delete (ADR-0004). See docs/architecture/broker-node-protocol.md.
PROTOCOL_VERSION = 2

_lock = threading.Lock()
_started = False
_client: BrokerClient | None = None


def start(plugin_dir: Path) -> None:
    global _started, _client
    with _lock:
        if _started:
            return
        _started = True  # mark first, so a config error can't cause repeated retries

        try:
            config = load_config(plugin_dir, protocol_version=PROTOCOL_VERSION)
        except Exception as error:  # noqa: BLE001 - never break ComfyUI startup
            log("Failed to load config; plugin idle.", error)
            return

        if not config.is_runnable:
            log(
                "Not configured (set GATEWAY_API_KEY via env, config.toml, or "
                "Settings -> StableArt Job Node); plugin idle."
            )
            return

        client = BrokerClient(config)
        thread = threading.Thread(target=client.run, name="job-plugin-broker", daemon=True)
        thread.start()
        _client = client
        atexit.register(stop)
        log(f'Started broker client "{config.node_name}" -> {config.broker_base_url}')


def stop() -> None:
    with _lock:
        if _client is not None:
            _client.shutdown()

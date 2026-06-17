"""Plugin configuration and node-id persistence.

Ports the env reads and ``loadPersistedNodeId`` / ``persistNodeId`` from
``node.ts``. Resolution order for each setting: environment variable, then
``config.toml`` in the plugin dir, then a default identical to the TS node.

Unlike the TS node (which ``throw``s when ``GATEWAY_API_KEY`` is missing), this
never raises — a misconfigured plugin must not break ComfyUI startup. Callers
check :attr:`PluginConfig.is_runnable`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # stdlib on 3.11+, backport on 3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .gpu import resolve_gpu_name
from .logging_util import log

# Maps a PluginConfig field to (env var, toml key). Defaults live in the dataclass
# below / the resolver, mirroring the TS node's `process.env.X || default`.
_ENV = {
    "broker_base_url": ("NODE_BROKER_URL", "broker_base_url"),
    "api_key": ("GATEWAY_API_KEY", "api_key"),
    "node_name": ("NODE_NAME", "node_name"),
    "gpu_name": ("NODE_GPU_NAME", "gpu_name"),
    "node_id_file": ("NODE_ID_FILE", "node_id_file"),
}
_ENV_INT = {
    "poll_interval_ms": ("COMFYUI_POLL_INTERVAL_MS", "poll_interval_ms", 1000),
    "heartbeat_interval_ms": ("HEARTBEAT_INTERVAL_MS", "heartbeat_interval_ms", 15000),
    "job_timeout_ms": ("NODE_JOB_TIMEOUT_MS", "job_timeout_ms", 120000),
}

# ComfyUI Settings-panel ids (set by web/job_plugin.js) → PluginConfig field.
# These are the lowest-priority source: env vars and config.toml override them.
_COMFY_SETTING_KEYS = {
    "stableart.brokerUrl": "broker_base_url",
    "stableart.apiKey": "api_key",
    "stableart.nodeName": "node_name",
}

# The broker the node connects to by default (operators rarely change it).
DEFAULT_BROKER_URL = "https://broker.stableart.io"


@dataclass(frozen=True)
class PluginConfig:
    plugin_dir: Path
    broker_base_url: str
    api_key: str
    node_name: str
    poll_interval_ms: int
    heartbeat_interval_ms: int
    job_timeout_ms: int
    node_id_file: Path
    gpu_name: str
    protocol_version: int

    @property
    def is_runnable(self) -> bool:
        """True when the minimum config to connect is present."""
        return bool(self.broker_base_url) and bool(self.api_key)


def _load_toml(plugin_dir: Path) -> dict[str, Any]:
    path = plugin_dir / "config.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        log("Failed to read config.toml, ignoring it:", error)
        return {}


def _comfy_settings_candidates(plugin_dir: Path) -> list[Path]:
    """Possible locations of ComfyUI's ``comfy.settings.json``.

    Prefer ``folder_paths`` (honors ``--user-directory``), but always include a
    path derived from the node's own location — ``folder_paths`` isn't reliably
    resolvable at the moment ComfyUI imports a custom node, and the node lives at
    ``<ComfyUI>/custom_nodes/<node>/`` so the settings file is two levels up.
    """
    candidates: list[Path] = []
    try:  # provided by ComfyUI at runtime; absent in tests / standalone use
        import folder_paths  # type: ignore[import-not-found]

        get_user_dir = getattr(folder_paths, "get_user_directory", None)
        user_dir = (
            get_user_dir()
            if callable(get_user_dir)
            else getattr(folder_paths, "user_directory", None)
        )
        if user_dir:
            candidates.append(Path(user_dir) / "default" / "comfy.settings.json")
        base_path = getattr(folder_paths, "base_path", None)
        if base_path:
            candidates.append(Path(base_path) / "user" / "default" / "comfy.settings.json")
    except Exception:  # noqa: BLE001 - fall back to the node-relative path below
        pass
    candidates.append(plugin_dir.parent.parent / "user" / "default" / "comfy.settings.json")
    return candidates


def _load_comfy_settings(plugin_dir: Path) -> dict[str, Any]:
    """Read config from ComfyUI's persisted Settings panel (the lowest-priority
    source). Best-effort: returns {} when the settings file is absent/unreadable.
    Values are returned keyed by PluginConfig field name so they merge with the
    config.toml dict.
    """
    raw: dict[str, Any] | None = None
    candidates = _comfy_settings_candidates(plugin_dir)
    for path in candidates:
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf8"))
                break
        except (OSError, ValueError):
            continue
    if not isinstance(raw, dict):
        return {}

    mapped: dict[str, Any] = {}
    for setting_id, field in _COMFY_SETTING_KEYS.items():
        value = raw.get(setting_id)
        if isinstance(value, str) and value.strip():
            mapped[field] = value
    return mapped


def _resolve_str(file_cfg: dict[str, Any], field: str, default: str | None) -> str | None:
    env_key, toml_key = _ENV[field]
    value = os.environ.get(env_key)
    # An empty/whitespace env var counts as unset (matches the TS node's `||`),
    # so e.g. `GATEWAY_API_KEY=` from a compose file falls through to config.toml
    # / the ComfyUI Settings panel instead of shadowing them.
    if value is None or not value.strip():
        value = file_cfg.get(toml_key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _resolve_int(file_cfg: dict[str, Any], field: str) -> int:
    env_key, toml_key, default = _ENV_INT[field]
    raw = os.environ.get(env_key)
    # An empty/whitespace env var counts as unset (matches _resolve_str), so an
    # empty compose var like `HEARTBEAT_INTERVAL_MS=` falls through to config.toml
    # instead of forcing the hardcoded default.
    if raw is None or not str(raw).strip():
        raw = file_cfg.get(toml_key)
    if raw is None:
        return default
    try:
        return int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return default


def load_config(plugin_dir: Path, *, protocol_version: int = 1) -> PluginConfig:
    # Source precedence (highest first): env var (handled in the resolvers) >
    # config.toml > ComfyUI Settings panel > default. Merge the two file-like
    # sources so config.toml wins over the panel.
    file_cfg = {**_load_comfy_settings(plugin_dir), **_load_toml(plugin_dir)}

    broker_base_url = (
        _resolve_str(file_cfg, "broker_base_url", DEFAULT_BROKER_URL) or DEFAULT_BROKER_URL
    ).rstrip("/")
    api_key = _resolve_str(file_cfg, "api_key", "") or ""
    node_name = _resolve_str(file_cfg, "node_name", "ComfyUI Plugin Node") or "ComfyUI Plugin Node"
    gpu_name = resolve_gpu_name(_resolve_str(file_cfg, "gpu_name", None))

    node_id_file_raw = _resolve_str(file_cfg, "node_id_file", None)
    node_id_file = (
        Path(node_id_file_raw)
        if node_id_file_raw
        else plugin_dir / "data" / "node-id"
    )
    if not node_id_file.is_absolute():
        node_id_file = plugin_dir / node_id_file

    return PluginConfig(
        plugin_dir=plugin_dir,
        broker_base_url=broker_base_url,
        api_key=api_key,
        node_name=node_name,
        poll_interval_ms=_resolve_int(file_cfg, "poll_interval_ms"),
        heartbeat_interval_ms=_resolve_int(file_cfg, "heartbeat_interval_ms"),
        job_timeout_ms=_resolve_int(file_cfg, "job_timeout_ms"),
        node_id_file=node_id_file,
        gpu_name=gpu_name,
        protocol_version=protocol_version,
    )


def read_node_id(path: Path) -> int | None:
    """Read the persisted node id; tolerate missing/garbage files. Ports
    ``loadPersistedNodeId``."""
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf8").strip()
    except OSError as error:
        log("Failed to read persisted node id", error)
        return None
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def write_node_id(path: Path, node_id: int) -> None:
    """Persist the assigned node id. Ports ``persistNodeId``; swallows errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(node_id), encoding="utf8")
    except OSError as error:
        log("Failed to persist node id", error)

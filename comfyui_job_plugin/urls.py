"""Pure URL helpers for the broker connection. Ports ``toWebsocketUrl`` /
``buildConnectionUrl`` from ``node.ts``."""

from __future__ import annotations

from urllib.parse import urlencode


def to_websocket_url(url: str) -> str:
    """Upgrade an http(s) URL to ws(s); leave ws(s) URLs untouched."""
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


def build_connection_url(
    broker_base_url: str,
    *,
    name: str,
    gpu: str,
    node_id: int | None,
    protocol_version: int,
    plugin_version: str | None = None,
) -> str:
    """Build the ``/nodes/connect`` WebSocket URL with query params.

    ``nodeId`` is only included when known (reconnect). ``protocol`` is always
    sent so the broker can detect this is a versioned client. ``plugin_version``,
    when set, is reported so the dashboard can show which build a node is running.
    """
    base = broker_base_url.rstrip("/")
    params: list[tuple[str, str]] = [("name", name)]
    if node_id is not None:
        params.append(("nodeId", str(node_id)))
    if gpu:
        params.append(("gpu", gpu))
    params.append(("protocol", str(protocol_version)))
    if plugin_version:
        params.append(("version", plugin_version))
    return to_websocket_url(f"{base}/nodes/connect?{urlencode(params)}")

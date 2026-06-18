"""Model file management on the node: locate the ComfyUI models dir, download a
catalog model into the right folder (checksum-verified), and delete one.

Only the platform broker can trigger these (operator-curated catalog), but we
still treat the wire input as untrusted: the target folder must be on an
allowlist and the filename may not escape it (no absolute paths / ``..``).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import requests

from .logging_util import log

# Only fetch over real HTTP(S); the catalog URL is operator-curated but still
# untrusted wire input. The cap/deadline below stop a malicious or misbehaving
# server from filling the operator's disk or pinning the single mgmt worker.
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_DOWNLOAD_BYTES = 80 * 1024**3  # 80 GiB — larger than any real single model
_MAX_DOWNLOAD_SECONDS = 6 * 3600  # wall-clock deadline (slow-loris guard)

# ComfyUI models subfolders we allow installs into. Keep in sync with what the
# workflow builders reference (checkpoints / diffusion_models / vae / text_encoders).
ALLOWED_FOLDERS = frozenset(
    {
        "checkpoints",
        "diffusion_models",
        "vae",
        "text_encoders",
        "clip",
        "loras",
        "controlnet",
        "upscale_models",
    }
)

# A progress callback receives (bytes_downloaded, total_bytes_or_None).
ProgressFn = Callable[[int, int | None], None]

_DEFAULT_MODELS_DIR = Path("/root/ComfyUI/models")


def resolve_models_dir() -> Path:
    """Locate ComfyUI's models directory (prefers ``folder_paths``)."""
    try:  # available inside a running ComfyUI
        import folder_paths  # type: ignore[import-not-found]

        models_dir = getattr(folder_paths, "models_dir", None)
        if models_dir:
            return Path(models_dir)
        base = getattr(folder_paths, "base_path", None)
        if base:
            return Path(base) / "models"
    except Exception:  # noqa: BLE001 - fall back below
        pass
    return _DEFAULT_MODELS_DIR


def safe_target_path(models_dir: Path, folder: str, filename: str) -> Path:
    """Resolve ``<models_dir>/<folder>/<filename>`` and reject anything that
    isn't on the folder allowlist or escapes the target folder."""
    if folder not in ALLOWED_FOLDERS:
        raise ValueError(f"folder not allowed: {folder!r}")
    rel = Path(filename)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError(f"unsafe filename: {filename!r}")
    target_root = (models_dir / folder).resolve()
    dest = (target_root / rel).resolve()
    # dest must be a strict descendant of target_root — this also rejects dest
    # resolving to target_root itself (a dir is never in its own .parents).
    if target_root not in dest.parents:
        raise ValueError(f"path escapes target folder: {filename!r}")
    return dest


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file_sha256(path: Path, expected: str) -> bool:
    """True iff ``path`` exists and its SHA-256 equals ``expected`` (case-insensitive)."""
    try:
        return _hash_file(path).lower() == expected.lower()
    except OSError:
        return False


def download_model(
    url: str,
    dest: Path,
    *,
    sha256: str | None = None,
    expected_size: int | None = None,
    auth_token: str | None = None,
    on_progress: ProgressFn | None = None,
    stop: threading.Event | None = None,
    chunk_size: int = 1 << 20,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    max_seconds: float = _MAX_DOWNLOAD_SECONDS,
) -> int:
    """Stream ``url`` to ``dest`` (atomic temp→rename), verifying ``sha256`` if
    given. Returns the byte count.

    Only http/https is allowed, and the transfer is bounded by ``max_bytes`` (a
    hard disk-fill cap, tightened to ``expected_size`` when the catalog declares
    one) and a ``max_seconds`` wall-clock deadline (slow-loris guard). When
    ``auth_token`` is set it is sent as a Bearer header for the first request
    (e.g. gated Civitai downloads); ``requests`` strips it on the cross-host CDN
    redirect, which is fine since the redirect target is pre-signed. Raises on
    HTTP error, checksum mismatch, cap/deadline breach, or abort (``stop`` set).
    Never leaves a partial file at ``dest``."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsupported url scheme: {scheme or '(none)'!r}")
    cap = min(max_bytes, int(expected_size * 1.05)) if expected_size else max_bytes
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    downloaded = 0
    started = time.monotonic()
    try:
        with requests.get(url, stream=True, timeout=60, headers=headers) as response:
            response.raise_for_status()
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
                total = content_length if content_length > 0 else None
            except (TypeError, ValueError):
                total = None  # missing/malformed/chunked/negative — size unknown
            if total is not None and total > cap:
                raise ValueError(f"download exceeds size cap ({total} > {cap} bytes)")
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if stop is not None and stop.is_set():
                        raise RuntimeError("aborted")
                    if time.monotonic() - started > max_seconds:
                        raise TimeoutError("download exceeded time limit")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if downloaded > cap:
                        raise ValueError(f"download exceeds size cap ({cap} bytes)")
                    if on_progress is not None:
                        on_progress(downloaded, total)
        if sha256:
            got = digest.hexdigest()
            if got.lower() != sha256.lower():
                raise ValueError(f"sha256 mismatch: expected {sha256}, got {got}")
        elif expected_size is not None and downloaded != expected_size:
            # No checksum to verify against, so guard truncation: a short 200
            # response under the cap must not be renamed in and reported installed.
            raise ValueError(
                f"size mismatch: expected {expected_size} bytes, got {downloaded}"
            )
        os.replace(tmp, dest)
        return downloaded
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as error:  # pragma: no cover
            log("Failed to clean up partial download", error)


def delete_model(models_dir: Path, folder: str, filename: str) -> bool:
    """Delete a model file (path-safe). Returns True if a file was removed."""
    dest = safe_target_path(models_dir, folder, filename)
    if not dest.is_file():
        return False  # missing, or a directory — unlink would raise
    dest.unlink()
    return True


def merge_installed_file(
    models: list[str],
    models_by_folder: dict[str, list[str]] | None,
    folder: str,
    filename: str,
) -> None:
    """Ensure a just-installed file appears in a reported model snapshot even when
    ComfyUI's (cached) folder listing hasn't picked it up yet.

    A download writes ``folder/filename`` straight to disk, but ComfyUI's HTTP model
    listing can keep returning a stale list for a moment afterwards — so a snapshot
    built purely from it would omit the new file, and the gateway's install-truth
    gate would reject jobs that need it. Merging the file the node *knows* it just
    installed closes that window. Mutates the passed structures in place.
    """
    if not folder or not filename:
        return
    if models_by_folder is not None:
        files = models_by_folder.setdefault(folder, [])
        if filename not in files:
            files.append(filename)
    # The flat list is checkpoints + diffusion only (the picker / v1 reconciliation).
    if folder in ("checkpoints", "diffusion_models") and filename not in models:
        models.append(filename)

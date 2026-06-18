import hashlib
import threading

import pytest

from comfyui_job_plugin import models
from comfyui_job_plugin.models import (
    delete_model,
    download_model,
    merge_installed_file,
    safe_target_path,
    verify_file_sha256,
)


def test_merge_installed_file_adds_missing_folder_entry():
    # A stale ComfyUI listing omitted the just-installed upscale model.
    by_folder: dict[str, list[str]] = {}
    flat: list[str] = []
    merge_installed_file(flat, by_folder, "upscale_models", "4x-UltraSharp.pth")
    assert by_folder["upscale_models"] == ["4x-UltraSharp.pth"]
    # Upscale models aren't in the flat checkpoint list.
    assert flat == []


def test_merge_installed_file_is_idempotent_and_handles_flat():
    by_folder = {"checkpoints": ["a.safetensors"]}
    flat = ["a.safetensors"]
    merge_installed_file(flat, by_folder, "checkpoints", "a.safetensors")
    assert by_folder["checkpoints"] == ["a.safetensors"]  # no duplicate
    assert flat == ["a.safetensors"]
    # A checkpoint not yet in the (cached) flat list is added.
    merge_installed_file(flat, by_folder, "checkpoints", "b.safetensors")
    assert by_folder["checkpoints"] == ["a.safetensors", "b.safetensors"]
    assert flat == ["a.safetensors", "b.safetensors"]


def test_merge_installed_file_tolerates_no_snapshot_and_blanks():
    flat: list[str] = []
    merge_installed_file(flat, None, "upscale_models", "x.pth")  # no by-folder snapshot
    merge_installed_file(flat, {}, "", "x.pth")  # blank folder
    merge_installed_file(flat, {}, "upscale_models", "")  # blank filename
    assert flat == []


def test_safe_target_path_allows_listed_folder_and_subdirs(tmp_path):
    assert safe_target_path(tmp_path, "checkpoints", "a.safetensors") == (
        tmp_path / "checkpoints" / "a.safetensors"
    ).resolve()
    # filenames may carry a subfolder (e.g. the job's "IL/novaAnimeXL_...")
    assert safe_target_path(tmp_path, "checkpoints", "IL/nova.safetensors") == (
        tmp_path / "checkpoints" / "IL" / "nova.safetensors"
    ).resolve()


def test_safe_target_path_rejects_unlisted_folder(tmp_path):
    with pytest.raises(ValueError):
        safe_target_path(tmp_path, "etc", "passwd")


@pytest.mark.parametrize("bad", ["../evil.bin", "/abs/evil.bin", "a/../../evil.bin", ""])
def test_safe_target_path_rejects_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        safe_target_path(tmp_path, "checkpoints", bad)


class _FakeResponse:
    def __init__(self, chunks, headers=None, status=200):
        self._chunks = chunks
        self.headers = headers or {}
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_get(monkeypatch, chunks, headers=None, status=200, capture=None):
    def _get(*args, **kwargs):
        if capture is not None:
            capture.update(kwargs)
            capture["url"] = args[0] if args else kwargs.get("url")
        return _FakeResponse(chunks, headers, status)

    monkeypatch.setattr(models.requests, "get", _get)


def test_download_writes_atomically_and_verifies_sha(tmp_path, monkeypatch):
    sha = hashlib.sha256(b"hello world").hexdigest()
    _patch_get(monkeypatch, [b"hello ", b"world"], headers={"Content-Length": "11"})
    dest = tmp_path / "checkpoints" / "x.bin"
    seen = []
    n = download_model(
        "http://x/x.bin", dest, sha256=sha, on_progress=lambda d, t: seen.append((d, t))
    )
    assert n == 11
    assert dest.read_bytes() == b"hello world"
    assert seen[-1] == (11, 11)  # progress reached total
    assert not dest.with_name("x.bin.part").exists()  # temp cleaned up


def test_download_sha_mismatch_leaves_no_file(tmp_path, monkeypatch):
    _patch_get(monkeypatch, [b"abc"])
    dest = tmp_path / "checkpoints" / "x.bin"
    with pytest.raises(ValueError, match="sha256 mismatch"):
        download_model("http://x", dest, sha256="0" * 64)
    assert not dest.exists()
    assert not dest.with_name("x.bin.part").exists()


def test_download_abort_leaves_no_file(tmp_path, monkeypatch):
    _patch_get(monkeypatch, [b"a", b"b", b"c"])
    stop = threading.Event()
    stop.set()
    dest = tmp_path / "checkpoints" / "x.bin"
    with pytest.raises(RuntimeError):
        download_model("http://x", dest, stop=stop)
    assert not dest.exists()


def test_download_rejects_non_http_scheme(tmp_path, monkeypatch):
    # No network call should happen for a bad scheme.
    def _boom(*a, **k):
        raise AssertionError("requests.get should not be called")

    monkeypatch.setattr(models.requests, "get", _boom)
    dest = tmp_path / "checkpoints" / "x.bin"
    for bad in ("file:///etc/passwd", "ftp://host/x", "/local/path"):
        with pytest.raises(ValueError, match="scheme"):
            download_model(bad, dest)


def test_download_rejects_oversize_content_length(tmp_path, monkeypatch):
    _patch_get(monkeypatch, [b"x"], headers={"Content-Length": "1000"})
    dest = tmp_path / "checkpoints" / "x.bin"
    with pytest.raises(ValueError, match="size cap"):
        download_model("http://x", dest, expected_size=10)
    assert not dest.exists()


def test_download_rejects_short_response_without_sha(tmp_path, monkeypatch):
    # No checksum: a truncated body under the cap must be rejected, not installed.
    _patch_get(monkeypatch, [b"short"])  # 5 bytes
    dest = tmp_path / "checkpoints" / "x.bin"
    with pytest.raises(ValueError, match="size mismatch"):
        download_model("http://x", dest, expected_size=100)
    assert not dest.exists()
    assert not dest.with_name("x.bin.part").exists()


def test_download_accepts_exact_size_without_sha(tmp_path, monkeypatch):
    _patch_get(monkeypatch, [b"12345"])  # 5 bytes
    dest = tmp_path / "checkpoints" / "x.bin"
    assert download_model("http://x", dest, expected_size=5) == 5
    assert dest.read_bytes() == b"12345"


def test_download_aborts_when_stream_exceeds_cap(tmp_path, monkeypatch):
    # No Content-Length, but the streamed bytes blow past the derived cap.
    _patch_get(monkeypatch, [b"a" * 8, b"b" * 8])
    dest = tmp_path / "checkpoints" / "x.bin"
    with pytest.raises(ValueError, match="size cap"):
        download_model("http://x", dest, expected_size=10)
    assert not dest.exists()
    assert not dest.with_name("x.bin.part").exists()


def test_download_tolerates_malformed_content_length(tmp_path, monkeypatch):
    _patch_get(monkeypatch, [b"hello"], headers={"Content-Length": "not-a-number"})
    dest = tmp_path / "checkpoints" / "x.bin"
    seen = []
    n = download_model("http://x", dest, on_progress=lambda d, t: seen.append((d, t)))
    assert n == 5
    assert dest.read_bytes() == b"hello"
    assert seen[-1][1] is None  # total unknown, progress reported with None


def test_download_sends_bearer_header_when_token_given(tmp_path, monkeypatch):
    captured: dict = {}
    _patch_get(monkeypatch, [b"data"], capture=captured)
    dest = tmp_path / "checkpoints" / "x.bin"
    download_model("http://x/x.bin", dest, auth_token="secret-token")
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}


def test_download_no_auth_header_without_token(tmp_path, monkeypatch):
    captured: dict = {}
    _patch_get(monkeypatch, [b"data"], capture=captured)
    dest = tmp_path / "checkpoints" / "x.bin"
    download_model("http://x/x.bin", dest)
    assert captured["headers"] is None


def test_verify_file_sha256(tmp_path):
    dest = tmp_path / "x.bin"
    dest.write_bytes(b"hello world")
    sha = hashlib.sha256(b"hello world").hexdigest()
    assert verify_file_sha256(dest, sha.upper()) is True  # case-insensitive
    assert verify_file_sha256(dest, "0" * 64) is False
    assert verify_file_sha256(tmp_path / "missing.bin", sha) is False


def test_delete_model(tmp_path):
    dest = tmp_path / "checkpoints" / "x.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x")
    assert delete_model(tmp_path, "checkpoints", "x.bin") is True
    assert not dest.exists()
    assert delete_model(tmp_path, "checkpoints", "x.bin") is False  # already gone

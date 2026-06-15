import pytest
import requests

from comfyui_job_plugin.comfy_api import ComfyApiClient, ComfyApiError


class _Resp:
    def __init__(self, status=200, payload=None, ok=None):
        self.status_code = status
        self._payload = [] if payload is None else payload
        self.ok = (200 <= status < 300) if ok is None else ok

    def json(self):
        return self._payload


class _Session:
    """Maps the requested folder (last URL segment) to a _Resp or an Exception."""

    def __init__(self, responses):
        self._responses = responses

    def get(self, url, timeout=None):
        folder = url.rsplit("/", 1)[-1]
        r = self._responses.get(folder, _Resp(200, []))
        if isinstance(r, Exception):
            raise r
        return r


def _client(responses):
    client = ComfyApiClient("http://x", 1000)
    client._session = _Session(responses)
    return client


def test_list_folder_none_on_http_error():
    assert _client({"loras": _Resp(500, ok=False)})._list_folder("loras") is None


def test_list_folder_none_on_request_exception():
    boom = requests.RequestException("timeout")
    assert _client({"loras": boom})._list_folder("loras") is None


def test_list_folder_empty_on_404():
    assert _client({"loras": _Resp(404, ok=False)})._list_folder("loras") == []


def test_list_folder_dedupes_names():
    resp = _Resp(200, ["a.safetensors", "a.safetensors", "b.ckpt"])
    assert _client({"checkpoints": resp})._list_folder("checkpoints") == [
        "a.safetensors",
        "b.ckpt",
    ]


def test_fetch_models_by_folder_skips_whole_snapshot_on_any_failure():
    # checkpoints listable, loras transiently failing → no partial snapshot.
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "loras": requests.RequestException("timeout"),
    }
    assert _client(responses).fetch_models_by_folder() is None


def test_fetch_models_by_folder_omits_empty_folders():
    responses = {f: _Resp(200, []) for f in ComfyApiClient._MANAGED_FOLDERS}
    responses["vae"] = _Resp(200, ["sdxl_vae.safetensors"])
    assert _client(responses).fetch_models_by_folder() == {
        "vae": ["sdxl_vae.safetensors"]
    }


def test_fetch_available_models_none_when_a_folder_fails():
    # A failed checkpoints listing must not yield a partial list (would clobber
    # the broker registry); it returns None so the caller skips the report.
    assert _client({"checkpoints": _Resp(500, ok=False)}).fetch_available_models() is None


def test_wait_for_outputs_aborts_promptly_when_no_longer_active():
    # should_abort is checked before any poll/sleep, so an abandoned job exits at
    # once instead of blocking the single-worker executor until timeout.
    with pytest.raises(ComfyApiError):
        _client({}).wait_for_outputs("pid", 10_000, should_abort=lambda: True)

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


# --- list_model_folders -------------------------------------------------------


def test_list_model_folders_returns_registered_names():
    resp = _Resp(200, ["checkpoints", "loras", "upscale_models"])
    assert _client({"models": resp}).list_model_folders() == [
        "checkpoints",
        "loras",
        "upscale_models",
    ]


def test_list_model_folders_none_when_endpoint_missing():
    assert _client({"models": _Resp(404, ok=False)}).list_model_folders() is None


def test_list_model_folders_none_on_non_list():
    assert _client({"models": _Resp(200, {"x": 1})}).list_model_folders() is None


def test_list_model_folders_caches_after_first_success():
    client = _client({"models": _Resp(200, ["checkpoints"])})
    assert client.list_model_folders() == ["checkpoints"]
    # Swap what ComfyUI would return; the cached value must still come back (no
    # second HTTP call on the per-job path).
    client._session = _Session({"models": _Resp(200, ["vae"])})
    assert client.list_model_folders() == ["checkpoints"]


def test_list_model_folders_does_not_cache_a_failure():
    client = _client({"models": _Resp(503, ok=False)})
    assert client.list_model_folders() is None
    # A later success is not masked by a cached failure.
    client._session = _Session({"models": _Resp(200, ["checkpoints"])})
    assert client.list_model_folders() == ["checkpoints"]


# --- fetch_available_models (shares the discovered folder list) ----------------


def test_fetch_available_models_skips_unregistered_diffusion_models():
    # This build doesn't register diffusion_models (absent from `available`), so it's
    # never queried — an unregistered diffusion_models can't sink the flat report.
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "diffusion_models": _Resp(500, ok=False),  # would error if queried
    }
    assert _client(responses).fetch_available_models(["checkpoints"]) == ["c.safetensors"]


def test_fetch_available_models_includes_registered_diffusion_models():
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "diffusion_models": _Resp(200, ["flux.safetensors"]),
    }
    got = _client(responses).fetch_available_models(["checkpoints", "diffusion_models"])
    assert got == ["c.safetensors", "flux.safetensors"]


def test_fetch_available_models_none_when_checkpoints_fail():
    # checkpoints is always queried; a failure there must not yield a partial list.
    responses = {"checkpoints": _Resp(500, ok=False)}
    assert _client(responses).fetch_available_models(["checkpoints"]) is None


def test_fetch_available_models_queries_both_when_available_none():
    # /models unavailable (None) → query both; a diffusion_models failure → None.
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "diffusion_models": _Resp(500, ok=False),
    }
    assert _client(responses).fetch_available_models(None) is None


# --- fetch_models_by_folder (takes the discovered folder list) -----------------


def test_fetch_models_by_folder_empty_when_comfyui_registers_none():
    # available=[] → ComfyUI registers no model folders → query none, report {}.
    responses = {"checkpoints": requests.RequestException("should not be queried")}
    assert _client(responses).fetch_models_by_folder([]) == {}


def test_fetch_models_by_folder_only_queries_registered_folders():
    # text_encoders isn't registered (absent from `available`) → never queried (it
    # would 500) — only the registered folders are listed.
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "upscale_models": _Resp(200, ["4x-UltraSharp.pth"]),
        "text_encoders": _Resp(500, ok=False),
    }
    got = _client(responses).fetch_models_by_folder(["checkpoints", "upscale_models"])
    assert got == {
        "checkpoints": ["c.safetensors"],
        "upscale_models": ["4x-UltraSharp.pth"],
    }


def test_fetch_models_by_folder_mirrors_clip_alias_onto_text_encoders():
    # Modern ComfyUI merged `clip` into `text_encoders`: /models lists only
    # text_encoders, /models/clip would 500. The text_encoders snapshot is mirrored
    # under the legacy `clip` key so the gateway can reconcile clip catalog entries.
    responses = {
        "text_encoders": _Resp(200, ["clip_l.safetensors"]),
        "clip": _Resp(500, ok=False),  # would sink the snapshot if queried
    }
    got = _client(responses).fetch_models_by_folder(["text_encoders"])
    assert got == {
        "text_encoders": ["clip_l.safetensors"],
        "clip": ["clip_l.safetensors"],
    }


def test_fetch_models_by_folder_keeps_real_clip_listing_when_registered():
    # When ComfyUI registers `clip` as its own folder, its real listing is queried
    # and wins — no mirroring from text_encoders.
    responses = {
        "text_encoders": _Resp(200, ["t5.safetensors"]),
        "clip": _Resp(200, ["clip_g.safetensors"]),
    }
    got = _client(responses).fetch_models_by_folder(["text_encoders", "clip"])
    assert got == {
        "text_encoders": ["t5.safetensors"],
        "clip": ["clip_g.safetensors"],
    }


def test_fetch_models_by_folder_none_when_a_queried_folder_fails():
    # A registered folder transiently fails → don't report a partial map (the gateway
    # reads the missing folder as deletions); skip the whole snapshot.
    responses = {
        "checkpoints": _Resp(200, ["c.safetensors"]),
        "loras": requests.RequestException("timeout"),
    }
    assert _client(responses).fetch_models_by_folder(["checkpoints", "loras"]) is None


def test_fetch_models_by_folder_falls_back_to_managed_when_available_none():
    # /models unavailable (None) → fall back to the full managed set.
    responses = {f: _Resp(200, []) for f in ComfyApiClient._MANAGED_FOLDERS}
    responses["vae"] = _Resp(200, ["sdxl_vae.safetensors"])
    assert _client(responses).fetch_models_by_folder(None) == {
        "vae": ["sdxl_vae.safetensors"]
    }


def test_fetch_models_by_folder_none_on_failure_in_fallback():
    # In the fallback, a folder failure still aborts (best-effort, no partial map).
    boom = requests.RequestException("down")
    responses = {f: boom for f in ComfyApiClient._MANAGED_FOLDERS}
    assert _client(responses).fetch_models_by_folder(None) is None


def test_wait_for_outputs_aborts_promptly_when_no_longer_active():
    # should_abort is checked before any poll/sleep, so an abandoned job exits at
    # once instead of blocking the single-worker executor until timeout.
    with pytest.raises(ComfyApiError):
        _client({}).wait_for_outputs("pid", 10_000, should_abort=lambda: True)

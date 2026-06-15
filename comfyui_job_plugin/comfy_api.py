"""Thin client over ComfyUI's stable HTTP API.

Ports ``submitWorkflow`` / ``waitForWorkflowOutputs`` / ``deleteComfyHistory`` /
``fetchAvailableModels`` and the ``/view`` + ``/upload/image`` calls from
``node.ts``. Uses ComfyUI's public endpoints only (no internals), per ADR-0003
phase 2. Failures raise :class:`ComfyApiError` carrying a machine ``code`` that
maps directly to the broker ``result`` error field.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from .logging_util import log


class ComfyApiError(Exception):
    def __init__(self, code: str, details: Any = None):
        super().__init__(code if details is None else f"{code}: {details}")
        self.code = code
        self.details = details


class ComfyApiClient:
    def __init__(self, base_url: str, poll_interval_ms: int):
        self._base_url = base_url.rstrip("/")
        self._poll_interval = max(0.05, poll_interval_ms / 1000)
        # requests.Session isn't safe to share across threads, and the job,
        # management, and models-report paths all use this client concurrently.
        # Hand out a per-thread Session instead.
        self._local = threading.local()

    @property
    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    @_session.setter
    def _session(self, value: requests.Session) -> None:
        self._local.session = value

    # ---- models -------------------------------------------------------------

    # ComfyUI model subfolders the platform can manage. The flat checkpoint list
    # (fetch_available_models) stays checkpoints+diffusion only so it can feed the
    # user-facing checkpoint picker; fetch_models_by_folder reports every managed
    # folder for the dashboard's per-node install reconciliation.
    _MANAGED_FOLDERS = (
        "checkpoints",
        "diffusion_models",
        "vae",
        "text_encoders",
        "clip",
        "loras",
        "controlnet",
        "upscale_models",
    )

    def _list_folder(self, folder: str) -> list[str] | None:
        """Installed filenames in a managed folder, or None if the listing failed.

        None (vs. an empty list) lets callers avoid reporting a partial snapshot
        on a transient error — otherwise a hiccup on one folder would look like
        "everything here was deleted". A 404 means the folder type isn't present
        on this ComfyUI, which is genuinely empty.
        """
        try:
            response = self._session.get(f"{self._base_url}/models/{folder}", timeout=15)
            if response.status_code == 404:
                return []
            if not response.ok:
                return None
            listed = response.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(listed, list):
            return None
        return list(dict.fromkeys(m for m in listed if isinstance(m, str)))

    def fetch_available_models(self) -> list[str] | None:
        """List installed checkpoints + diffusion models (deduped), or None if a
        listing failed.

        This flat list feeds the user-facing checkpoint picker, so it intentionally
        excludes vae/lora/etc.; use :meth:`fetch_models_by_folder` for reconciliation.
        Returns None (rather than a partial list) when a folder listing fails, so the
        caller can avoid clobbering the broker's registry with a half-empty list that
        would make valid checkpoints look missing.
        """
        models: list[str] = []
        for folder in ("checkpoints", "diffusion_models"):
            files = self._list_folder(folder)
            if files is None:
                return None
            if files:
                models.extend(files)
        return list(dict.fromkeys(models))

    def fetch_models_by_folder(self) -> dict[str, list[str]] | None:
        """Map each managed folder → its installed filenames (filesystem truth),
        or None if any folder listing failed.

        Lets the gateway reconcile install state for every managed folder, not just
        checkpoints. Empty folders are omitted to keep the payload small. Returning
        None on a partial failure tells the caller to skip the snapshot rather than
        report one that looks like deletions (the gateway then keeps the last good
        snapshot).
        """
        result: dict[str, list[str]] = {}
        for folder in self._MANAGED_FOLDERS:
            files = self._list_folder(folder)
            if files is None:
                return None
            if files:
                result[folder] = files
        return result

    # ---- prompt submission --------------------------------------------------

    def submit_workflow(self, prompt: dict[str, Any], client_prefix: str) -> str:
        """POST a workflow graph to ``/prompt``; return the ``prompt_id``."""
        url = f"{self._base_url}/prompt"
        log(f"Submitting prompt to ComfyUI at {url}")
        try:
            response = self._session.post(
                url,
                json={"prompt": prompt, "client_id": f"{client_prefix}-{time.time_ns()}"},
                timeout=30,
            )
        except requests.RequestException as error:
            raise ComfyApiError("internal_error", f"Cannot connect to ComfyUI: {error}") from error

        if not response.ok:
            details: Any
            try:
                if response.headers.get("Content-Type", "").startswith("application/json"):
                    details = response.json()
                else:
                    details = response.text
            except ValueError:
                details = response.text
            log(f"ComfyUI rejected prompt ({response.status_code})", details)
            raise ComfyApiError("comfyui_error", details)

        try:
            res_json = response.json()
        except ValueError as error:
            raise ComfyApiError("comfyui_error", "Invalid /prompt response") from error
        prompt_id = res_json.get("prompt_id") if isinstance(res_json, dict) else None
        if not prompt_id:
            raise ComfyApiError("comfyui_error", "Missing prompt id in response")
        return prompt_id

    # ---- polling ------------------------------------------------------------

    def wait_for_outputs(
        self,
        prompt_id: str,
        timeout_ms: int,
        should_abort: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Poll ``/history/<id>`` until outputs appear or the timeout elapses.

        ``should_abort`` is checked each iteration so an abandoned job (its
        connection dropped / a newer job took over) stops polling promptly and
        frees the single-worker executor instead of blocking it until timeout."""
        deadline = time.monotonic() + max(
            self._poll_interval, (timeout_ms / 1000) - self._poll_interval
        )
        while time.monotonic() < deadline:
            if should_abort is not None and should_abort():
                raise ComfyApiError("aborted", "job is no longer active")
            time.sleep(self._poll_interval)
            try:
                response = self._session.get(
                    f"{self._base_url}/history/{prompt_id}", timeout=15
                )
            except requests.RequestException as error:
                log("Failed to fetch ComfyUI history", error)
                continue
            if not response.ok:
                continue
            try:
                history_json = response.json()
            except ValueError as error:
                log("Failed to parse ComfyUI history response", error)
                continue
            if not isinstance(history_json, dict):
                continue  # unexpected shape (error page, array) — keep polling
            prompt_history = history_json.get(prompt_id)
            outputs = (
                prompt_history.get("outputs")
                if isinstance(prompt_history, dict)
                else None
            )
            if isinstance(outputs, dict):
                return outputs

        raise ComfyApiError(
            "timeout", "ComfyUI did not finish the workflow before the timeout elapsed"
        )

    def delete_history(self, prompt_id: str) -> None:
        """Fire-and-forget cleanup of a history entry; never fails the job."""
        try:
            self._session.post(
                f"{self._base_url}/history",
                json={"delete": [prompt_id]},
                timeout=10,
            )
        except requests.RequestException as error:
            log("Failed to delete ComfyUI history entry", error)

    # ---- assets -------------------------------------------------------------

    def view_image(self, filename: str, subfolder: str, type_: str) -> tuple[bytes, str]:
        """Download a generated image via ``/view``; return (bytes, content-type)."""
        response = self._session.get(
            f"{self._base_url}/view",
            params={"filename": filename, "subfolder": subfolder, "type": type_},
            timeout=60,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type") or "image/png"
        return response.content, content_type

    def upload_image(self, data: bytes, content_type: str, filename: str) -> str:
        """Upload an input image via ``/upload/image``; return its resolved name."""
        try:
            response = self._session.post(
                f"{self._base_url}/upload/image",
                files={"image": (filename, data, content_type)},
                timeout=60,
            )
        except requests.RequestException as error:
            raise ComfyApiError("internal_error", str(error)) from error

        if not response.ok:
            try:
                details: Any = response.text
            except (ValueError, OSError):
                details = response.reason
            raise ComfyApiError("comfyui_error", details)

        try:
            uploaded = response.json()
        except ValueError as error:
            raise ComfyApiError(
                "invalid_result", "Failed to parse ComfyUI upload response"
            ) from error

        if not isinstance(uploaded, dict):
            raise ComfyApiError(
                "invalid_result", "ComfyUI upload response was not a JSON object"
            )
        name = uploaded.get("name")
        if not name:
            raise ComfyApiError(
                "invalid_result", "ComfyUI upload response did not include a filename"
            )
        subfolder = uploaded.get("subfolder")
        return f"{subfolder}/{name}" if subfolder else name

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
        # ComfyUI's registered model folders are static for the process lifetime
        # (set at import), so cache them after the first successful /models call to
        # keep the per-job critical path off an extra HTTP round-trip.
        self._cached_model_folders: list[str] | None = None

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

    # Legacy folder name → its canonical replacement. Modern ComfyUI merged the old
    # `clip` directory into `text_encoders` (its paths include the legacy `clip/`
    # dir), so such builds list `text_encoders` from /models but not `clip`, and a
    # direct /models/clip 500s like any unregistered folder. We mirror the canonical
    # snapshot under the legacy key so the gateway can still reconcile catalog
    # entries filed under the old folder (see kindToFolder: clip → "clip").
    _LEGACY_FOLDER_ALIASES = {"clip": "text_encoders"}

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

    def fetch_available_models(self, available: list[str] | None) -> list[str] | None:
        """List installed checkpoints + diffusion models (deduped), or None if a
        required listing failed.

        This flat list feeds the user-facing checkpoint picker, so it intentionally
        excludes vae/lora/etc.; use :meth:`fetch_models_by_folder` for reconciliation.

        ``available`` is ComfyUI's registered model folders (from
        :meth:`list_model_folders`), shared with the per-folder report so we don't
        re-discover. ``diffusion_models`` only exists on newer builds, so it's queried
        only when registered (or when ``available`` is None — /models unavailable):
        otherwise an unregistered ``diffusion_models`` would 500 and sink the whole
        report. Returns None when a folder we *do* query fails, so the caller skips
        the report rather than clobbering the registry with a half-empty list.
        """
        folders = ["checkpoints"]
        if available is None or "diffusion_models" in available:
            folders.append("diffusion_models")
        models: list[str] = []
        for folder in folders:
            files = self._list_folder(folder)
            if files is None:
                return None
            if files:
                models.extend(files)
        return list(dict.fromkeys(models))

    def list_model_folders(self) -> list[str] | None:
        """ComfyUI's registered model folder names (``GET /models``), or None if the
        endpoint isn't available.

        Different ComfyUI builds register different model folders (e.g.
        ``text_encoders`` / ``diffusion_models`` only on newer versions). Asking
        ComfyUI which folders it actually has lets the reports query only those, so
        we never hit a managed folder this build doesn't know (which 500s and used to
        sink the whole snapshot).

        Cached after the first success (the folder set is static per process), so the
        per-job generation path doesn't repeat this HTTP call. A failure isn't cached,
        so a transient /models error retries next time."""
        if self._cached_model_folders is not None:
            return self._cached_model_folders
        try:
            response = self._session.get(f"{self._base_url}/models", timeout=15)
            if not response.ok:
                return None
            listed = response.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(listed, list):
            return None
        folders = [name for name in listed if isinstance(name, str)]
        self._cached_model_folders = folders
        return folders

    def fetch_models_by_folder(
        self, available: list[str] | None
    ) -> dict[str, list[str]] | None:
        """Map each managed folder → its installed filenames (filesystem truth),
        or None to skip the report.

        Lets the gateway reconcile install state for every managed folder, not just
        checkpoints. Empty folders are omitted to keep the payload small.

        Aligns to ComfyUI: ``available`` is ComfyUI's registered model folders (from
        :meth:`list_model_folders`), so we query only the managed folders this build
        registers — a folder it lacks is never queried (older builds return a 500, not
        a 404, for an unregistered folder, which is what used to sink the snapshot).
        An empty ``available`` means ComfyUI authoritatively registers no model
        folders, so we query none. ``available`` is None only when /models is
        unavailable, where we fall back to the full managed set.

        If a folder we *do* query fails to list, return None (skip the whole report)
        rather than a partial map: the gateway treats a folder absent from
        ``modelsByFolder`` as an authoritative empty, so a partial snapshot would mark
        that folder's installed models deleted until the next full report. Reporting
        None makes the gateway keep its last good snapshot. Thanks to discovery, a
        failure here is a transient/registered-folder error, not a missing folder. The
        None-fallback is best-effort: without discovery we can't tell an unregistered
        folder from a transient error, so a failure there still aborts (deliberately,
        to avoid the partial-snapshot problem); in practice every current ComfyUI
        exposes /models, so the fallback is rarely hit.
        """
        folders = (
            [f for f in self._MANAGED_FOLDERS if f in available]
            if available is not None
            else list(self._MANAGED_FOLDERS)
        )
        result: dict[str, list[str]] = {}
        for folder in folders:
            files = self._list_folder(folder)
            if files is None:
                log(f"Could not list model folder {folder!r}; skipping this snapshot")
                return None
            if files:
                result[folder] = files

        # Mirror canonical snapshots under any legacy alias this build merged away
        # (e.g. clip → text_encoders). Only when the alias isn't separately
        # registered — if ComfyUI still exposes it as its own folder we already
        # queried it above and its listing is authoritative (setdefault keeps it).
        # Skipped when available is None (the fallback queries both folders directly).
        if available is not None:
            for alias, canonical in self._LEGACY_FOLDER_ALIASES.items():
                if alias not in available and canonical in result:
                    result.setdefault(alias, result[canonical])
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

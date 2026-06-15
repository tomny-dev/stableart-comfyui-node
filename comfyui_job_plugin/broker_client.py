"""Background WebSocket client for the StableArt node broker.

Ports the connection lifecycle from ``node.ts`` (connect, welcome/handshake,
heartbeat, reconnect-with-backoff, job dispatch, result reporting) to a
thread-based model suitable for running inside ComfyUI. The whole client lives on
a daemon thread started by :mod:`runtime`; jobs run on a single-worker executor
so at most one job is in flight, matching the broker's one-job-per-node model.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import websocket  # websocket-client

from .comfy_api import ComfyApiClient
from .config import PluginConfig, read_node_id, write_node_id
from .jobs.handlers import JobFailure, JobResult, JobSuccess, execute_job
from .jobs.schemas import PayloadError, parse_job_payload
from .logging_util import log
from .server_ready import await_server_ready
from .urls import build_connection_url

_MAX_BACKOFF_MS = 30_000


def compute_backoff_ms(attempts: int) -> int:
    """Exponential backoff capped at 30s, mirroring the TS ``scheduleReconnect``."""
    # Cap the exponent: a prolonged disconnect grows `attempts` unboundedly, and
    # 2**attempts would balloon into a huge integer. 2**5 already exceeds the cap.
    return min(_MAX_BACKOFF_MS, 1000 * (2 ** min(attempts, 5)))


class BrokerClient:
    def __init__(self, config: PluginConfig):
        self._config = config
        self._known_node_id = read_node_id(config.node_id_file)
        self._stop = threading.Event()
        self._connection_attempts = 0
        self._ws: websocket.WebSocketApp | None = None
        self._comfy: ComfyApiClient | None = None

        # Heartbeat is per-connection; this event is replaced on each open.
        self._hb_stop = threading.Event()

        # websocket-client's send() is not safe to call from multiple threads
        # (heartbeat, job results, management progress, and the models report all
        # send concurrently). Serialize every frame through this lock.
        self._send_lock = threading.Lock()

        # One job at a time. The lock guards _current_job_id / _job_timer so the
        # completion path and the timeout timer can't both report a result.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-plugin-exec")
        self._job_lock = threading.Lock()
        self._current_job_id: str | None = None
        self._job_timer: threading.Timer | None = None

        # Management commands (model install/delete) run on their own worker so a
        # multi-GB download never blocks job execution.
        self._mgmt_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="job-plugin-mgmt"
        )

    # ---- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        """Thread entry: wait for local ComfyUI, then connect+reconnect forever."""
        base_url = await_server_ready(self._stop)
        if base_url is None:
            return  # stopped before the local server came up
        self._comfy = ComfyApiClient(base_url, self._config.poll_interval_ms)
        log(f"Local ComfyUI ready at {base_url}; connecting to broker.")

        while not self._stop.is_set():
            self._connect_once()
            if self._stop.is_set():
                break
            delay_ms = compute_backoff_ms(self._connection_attempts)
            log(f"Reconnecting in {delay_ms}ms...")
            self._stop.wait(delay_ms / 1000)

    def shutdown(self) -> None:
        self._stop.set()
        self._hb_stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception as error:  # noqa: BLE001
                log("Error closing broker socket on shutdown", error)
        self._executor.shutdown(wait=False)
        self._mgmt_executor.shutdown(wait=False)

    def _connect_once(self) -> None:
        self._connection_attempts += 1
        url = build_connection_url(
            self._config.broker_base_url,
            name=self._config.node_name,
            gpu=self._config.gpu_name,
            node_id=self._known_node_id,
            protocol_version=self._config.protocol_version,
        )
        descriptor = f" as node {self._known_node_id}" if self._known_node_id else ""
        log(f"Connecting to broker{descriptor} ({url}) [GPU: {self._config.gpu_name}]")

        self._ws = websocket.WebSocketApp(
            url,
            header=[f"x-api-key: {self._config.api_key}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        # Blocks until the socket closes; reconnect is handled by the run() loop.
        # ping_interval/ping_timeout actively probe the socket so a silently dead
        # connection (half-open TCP) is detected and run_forever returns promptly
        # instead of hanging forever.
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    # ---- socket callbacks ---------------------------------------------------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connection_attempts = 0
        log("Broker connection established.")
        self._start_heartbeat(ws)
        # Report available models off the socket thread so we never block reads.
        threading.Thread(target=self._report_models, args=(ws,), daemon=True).start()

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            log("Failed to parse broker message")
            return
        if not isinstance(message, dict):
            return

        msg_type = message.get("type")
        if msg_type == "welcome":
            self._handle_welcome(message)
        elif msg_type == "job":
            self._handle_job(message)
        elif msg_type in ("model.install", "model.delete"):
            self._handle_management(message)
        else:
            log("Received unknown message from broker", message)

    def _on_close(self, ws: websocket.WebSocketApp, code: Any, reason: Any) -> None:
        log(f"Broker connection closed ({code}) {reason or ''}")
        self._hb_stop.set()
        self._abandon_current_job()

    def _on_error(self, ws: websocket.WebSocketApp, error: Any) -> None:
        log("Broker connection error", error)
        # websocket-client does not always emit on_close after on_error, so mirror
        # the close path here: stop the heartbeat and abandon the current job.
        # Otherwise _current_job_id stays set and _handle_job would ignore work on
        # the reconnected socket as "another is in progress" until the stale job
        # times out.
        self._hb_stop.set()
        self._abandon_current_job()

    # ---- handshake & heartbeat ---------------------------------------------

    def _handle_welcome(self, message: dict[str, Any]) -> None:
        node_id = message.get("nodeId")
        if not isinstance(node_id, int):
            return
        if self._known_node_id != node_id:
            self._known_node_id = node_id
            write_node_id(self._config.node_id_file, node_id)
        broker_version = message.get("protocolVersion", 1)
        log(
            f"Connected to broker as node {node_id} "
            f"(broker protocol v{broker_version}, node v{self._config.protocol_version})"
        )

    def _start_heartbeat(self, ws: websocket.WebSocketApp) -> None:
        # Stop any prior heartbeat before replacing the event. on_close normally
        # does this, but an on_error not followed by on_close would otherwise
        # leak the previous thread once a new connection replaces _hb_stop.
        self._hb_stop.set()
        self._hb_stop = threading.Event()
        # Floor at 1s so a misconfigured 0/negative interval can't spin a tight
        # loop spamming heartbeats at 100% CPU.
        interval = max(1.0, self._config.heartbeat_interval_ms / 1000)

        def loop(stop: threading.Event) -> None:
            while not stop.wait(interval):
                if not self._ws_send(ws, {"type": "heartbeat"}):
                    return  # socket closing races the heartbeat

        threading.Thread(target=loop, args=(self._hb_stop,), daemon=True).start()

    def _report_models(self, ws: websocket.WebSocketApp) -> None:
        if self._comfy is None:
            return
        try:
            models = self._comfy.fetch_available_models()
            models_by_folder = self._comfy.fetch_models_by_folder()
        except Exception as error:  # noqa: BLE001
            log("Failed to fetch models", error)
            return
        if models is None:
            # A checkpoint/diffusion listing failed; skip the whole report so the
            # gateway keeps its last good registry instead of seeing a partial list.
            log("Skipping model report; a folder listing failed")
            return
        log(f"Reporting {len(models)} available models to broker")
        message: dict[str, Any] = {"type": "models", "models": models}
        # Omit the per-folder snapshot if any folder listing failed, so the gateway
        # keeps its last good snapshot instead of treating the gap as deletions.
        if models_by_folder is not None:
            message["modelsByFolder"] = models_by_folder
        if not self._ws_send(ws, message):
            log("Failed to send models")

    # ---- job dispatch -------------------------------------------------------

    def _handle_job(self, message: dict[str, Any]) -> None:
        job_id = message.get("id")
        if not isinstance(job_id, str) or not job_id:
            return

        with self._job_lock:
            if self._current_job_id is not None:
                log("Received job while another is in progress, ignoring.")
                return
            self._current_job_id = job_id
            raw_timeout = message.get("timeoutMs")
            try:
                timeout_ms = (
                    int(raw_timeout)
                    if raw_timeout is not None
                    else self._config.job_timeout_ms
                )
            except (TypeError, ValueError):
                timeout_ms = self._config.job_timeout_ms
            timeout_ms = max(1000, timeout_ms)
            timer = threading.Timer(timeout_ms / 1000, self._on_job_timeout, args=(job_id,))
            timer.daemon = True
            self._job_timer = timer
            timer.start()

        self._executor.submit(self._run_job, job_id, message.get("payload"), timeout_ms)

    def _run_job(self, job_id: str, raw_payload: Any, timeout_ms: int) -> None:
        try:
            payload = parse_job_payload(raw_payload)
        except PayloadError as error:
            result: JobResult = JobFailure("invalid_payload", str(error))
        else:
            try:
                if self._comfy is None:
                    raise RuntimeError("ComfyUI client not initialized")
                # Abort polling promptly if this job is no longer the active one
                # (connection dropped / a newer job took over), so the abandoned
                # job stops occupying the single-worker executor.
                result = execute_job(
                    payload,
                    timeout_ms,
                    self._comfy,
                    self._config,
                    should_abort=lambda: self._current_job_id != job_id,
                )
            except Exception as error:  # noqa: BLE001 - never let a job crash the worker
                result = JobFailure("internal_error", str(error))

        if self._claim_finish(job_id):
            self._send_result(job_id, result)

    def _on_job_timeout(self, job_id: str) -> None:
        if self._claim_finish(job_id):
            log(f"Job {job_id} timed out while waiting for ComfyUI response.")
            self._send_result(job_id, JobFailure("timeout"))

    def _claim_finish(self, job_id: str) -> bool:
        """Atomically finalize ``job_id`` if it is still current. Returns True for
        the single caller (worker or timer) that wins the race to report it."""
        with self._job_lock:
            if self._current_job_id != job_id:
                return False
            self._current_job_id = None
            if self._job_timer is not None:
                self._job_timer.cancel()
                self._job_timer = None
            return True

    def _abandon_current_job(self) -> None:
        with self._job_lock:
            self._current_job_id = None
            if self._job_timer is not None:
                self._job_timer.cancel()
                self._job_timer = None

    def _send_result(self, job_id: str, result: JobResult) -> None:
        ws = self._ws
        if ws is None:
            return
        if isinstance(result, JobSuccess):
            message: dict[str, Any] = {
                "type": "result",
                "id": job_id,
                "ok": True,
                "contentType": result.content_type,
                "data": result.data,
            }
            if result.seed is not None:
                message["seed"] = result.seed
        else:
            message = {"type": "result", "id": job_id, "ok": False, "error": result.error}
            if result.details is not None:
                message["details"] = result.details
        if not self._ws_send(ws, message):
            log("Failed to send result to broker")

    def _ws_send(self, ws: websocket.WebSocketApp, message: dict[str, Any]) -> bool:
        """Serialize one JSON frame onto ``ws`` under the send lock. Returns True
        on success. websocket-client has no internal send lock, so without this
        the heartbeat / result / management senders could interleave frames."""
        try:
            with self._send_lock:
                ws.send(json.dumps(message))
            return True
        except Exception:  # noqa: BLE001 - socket closing races concurrent senders
            return False

    def _send_json(self, message: dict[str, Any]) -> None:
        ws = self._ws
        if ws is not None and not self._ws_send(ws, message):
            log("Failed to send message to broker")

    # ---- management commands (model install / delete) -----------------------

    def _handle_management(self, message: dict[str, Any]) -> None:
        command_id = message.get("commandId")
        if not isinstance(command_id, str) or not command_id:
            return
        self._mgmt_executor.submit(self._run_management, message)

    def _run_management(self, message: dict[str, Any]) -> None:
        from . import models as model_ops

        command_id = message["commandId"]
        msg_type = message["type"]
        try:
            models_dir = model_ops.resolve_models_dir()
            if msg_type == "model.install":
                self._install_model(command_id, message, models_dir, model_ops)
            else:  # model.delete
                folder = str(message.get("folder") or "")
                filename = str(message.get("filename") or "")
                removed = model_ops.delete_model(models_dir, folder, filename)
                self._send_management_result(
                    command_id, True, details="deleted" if removed else "not present"
                )
        except Exception as error:  # noqa: BLE001 - report, never crash the worker
            log(f"Management command {msg_type} failed", error)
            self._send_management_result(command_id, False, error=str(error))
            return
        # Re-report installed models (filesystem truth) after a successful change.
        if self._ws is not None:
            self._report_models(self._ws)

    def _install_model(self, command_id, message, models_dir, model_ops) -> None:
        folder = str(message.get("folder") or "")
        filename = str(message.get("filename") or "")
        url = str(message.get("url") or "")
        raw_sha = message.get("sha256")
        sha256 = raw_sha if isinstance(raw_sha, str) and raw_sha else None
        raw_size = message.get("sizeBytes")
        expected_size = raw_size if isinstance(raw_size, int) and raw_size > 0 else None
        raw_token = message.get("authToken")
        auth_token = raw_token if isinstance(raw_token, str) and raw_token else None

        dest = model_ops.safe_target_path(models_dir, folder, filename)
        if dest.exists() and not dest.is_file():
            raise ValueError(f"target path exists but is not a file: {dest}")
        if dest.is_file():
            # Only trust an existing file when it matches the catalog checksum.
            # Without a checksum, fall back to a size match (when the catalog
            # declares one) so a truncated/0-byte/corrupt file isn't reported
            # installed; with neither signal, accept the existing file.
            if sha256 is not None:
                already_installed = model_ops.verify_file_sha256(dest, sha256)
            elif expected_size is not None:
                already_installed = dest.stat().st_size == expected_size
            else:
                already_installed = True
            if already_installed:
                self._send_management_progress(command_id, "done", 100)
                self._send_management_result(command_id, True, details="already installed")
                return
            log(f"Existing {folder}/{filename} doesn't match catalog; re-downloading")
        if not url:
            raise ValueError("missing download url")

        log(f"Installing model {folder}/{filename} from catalog")
        self._send_management_progress(command_id, "downloading", 0)
        last_pct = -1

        def on_progress(downloaded: int, total: int | None) -> None:
            nonlocal last_pct
            if not total:
                return  # unknown size: keep phase "downloading", don't fake a %
            pct = int(downloaded * 100 / total)
            if pct >= last_pct + 2 or pct == 100:  # throttle to ~every 2%
                last_pct = pct
                self._send_management_progress(command_id, "downloading", pct)

        model_ops.download_model(
            url,
            dest,
            sha256=sha256,
            expected_size=expected_size,
            auth_token=auth_token,
            on_progress=on_progress,
            stop=self._stop,
        )
        self._send_management_progress(command_id, "done", 100)
        self._send_management_result(command_id, True)

    def _send_management_progress(
        self, command_id: str, phase: str, progress: int | None = None
    ) -> None:
        msg: dict[str, Any] = {
            "type": "management.progress",
            "commandId": command_id,
            "phase": phase,
        }
        if progress is not None:
            msg["progress"] = progress
        self._send_json(msg)

    def _send_management_result(
        self, command_id: str, ok: bool, error: str | None = None, details: str | None = None
    ) -> None:
        msg: dict[str, Any] = {"type": "management.result", "commandId": command_id, "ok": ok}
        if error is not None:
            msg["error"] = error
        if details is not None:
            msg["details"] = details
        self._send_json(msg)

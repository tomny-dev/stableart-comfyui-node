# comfyui-job-plugin — AGENTS.md

A ComfyUI custom node (Python) that runs inside ComfyUI as a StableArt GPU worker: it connects to
the StableArt node broker over WebSocket and runs image-generation and `ai-caption` jobs against
the local ComfyUI.

> Developed in the StableArt platform monorepo (source of truth) and mirrored to this public repo
> for ComfyUI-Manager / Comfy Registry install. The broker↔node wire contract and architecture
> ADRs live in the platform repo. See `PUBLISHING.md` for the release pipeline.

## Quick commands

```bash
python -m venv .venv && ./.venv/Scripts/python -m pip install -e ".[dev]"   # Windows paths
./.venv/Scripts/python -m pytest      # unit tests — pure logic, no GPU/broker
./.venv/Scripts/python -m ruff check . # lint
```

## Validate before declaring done

- `pytest` and `ruff check .` must pass. The suite covers the pure logic (URL/handshake,
  checkpoint resolver, workflow builders + patch/no-mutation, caption extraction, payload
  schemas, backoff). That's necessary but **not** sufficient.
- The real signal is an end-to-end job round-trip: install into a ComfyUI with a GPU, set
  `NODE_BROKER_URL`/`GATEWAY_API_KEY`, and drive an `image_generation` and an `ai-caption` job
  through the gateway → broker → the plugin. Confirm the result returns, the `nodeId` persists
  across a ComfyUI restart, and reconnect works when the broker bounces.

## Things to know before editing

- **Don't block ComfyUI startup.** The top-level `__init__.py` only registers empty node
  mappings and calls `runtime.start()`, which spawns a daemon thread and returns immediately.
  All waiting (local-server readiness, broker connect, jobs) happens on that thread.
- **Never `raise` on misconfiguration.** Unlike the TS node (which `throw`s without
  `GATEWAY_API_KEY`), a missing config logs a warning and leaves the plugin idle — a throwing
  custom node degrades the operator's ComfyUI load. See `config.PluginConfig.is_runnable`.
- **Stable HTTP API only.** Use `/prompt`, `/history`, `/view`, `/upload/image`, `/models/*`
  (in `comfy_api.py`). ComfyUI internals (PromptQueue, executor) are off-limits — a later,
  optional optimization may subscribe to execution events, but the HTTP API is the stable contract.
- **One job at a time.** A single-worker executor + the `_current_job_id` guard in
  `broker_client.py` enforce it, mirroring the broker's one-job-per-node model. The completion
  path and the timeout `Timer` race through `_claim_finish` so only one reports a result.
- **Bundled workflow JSONs** live in `comfyui_job_plugin/workflows/data/`. They're loaded via
  `importlib.resources`, not relative paths, so they survive ComfyUI-Manager's install layout.
  Patch node-ids must match the JSON.
- **Protocol versioning.** This client sends `?protocol=1`; the broker echoes `protocolVersion`
  in `welcome`. Keep both in sync with the platform's broker↔node protocol contract; the broker
  bumps its supported version only on a breaking change.
- **Config has three sources** (precedence, highest first): env var → `config.toml` → ComfyUI
  Settings panel (`web/job_plugin.js` → `comfy.settings.json`, read in `config.py` via
  `folder_paths`) → default. Settings changes apply on the next ComfyUI restart (connection is
  made once at startup). Add a new setting in both `web/job_plugin.js` and `_COMFY_SETTING_KEYS`.
- **Keep `requirements.txt` in sync with `pyproject.toml` `dependencies`.** ComfyUI-Manager
  installs from `requirements.txt`; the registry/comfy-cli reads `pyproject`.
- **Log via `logging_util.log()`** (a shim over `logging.getLogger("comfyui-job-plugin")`), not
  `print` — it integrates with ComfyUI's logging config.

## Module map (port from `node.ts`)

| Concern                                                   | Module                                     |
| --------------------------------------------------------- | ------------------------------------------ |
| Config + nodeId persistence                               | `config.py`                                |
| ComfyUI Settings-panel config UI                          | `web/job_plugin.js` (+ `config.py` reader) |
| Broker connection URL / ws-upgrade                        | `urls.py`                                  |
| GPU detection                                             | `gpu.py`                                   |
| Local-server readiness / port discovery                   | `server_ready.py`                          |
| ComfyUI HTTP calls                                        | `comfy_api.py`                             |
| Workflow load/patch                                       | `workflows/loader.py`                      |
| Checkpoint fuzzy match                                    | `workflows/checkpoints.py`                 |
| Image workflow builder                                    | `workflows/image.py`                       |
| Caption extraction / filenames                            | `workflows/caption.py`                     |
| Payload validation                                        | `jobs/schemas.py`                          |
| Job execution                                             | `jobs/handlers.py`                         |
| WS client / reconnect / heartbeat / dispatch / management | `broker_client.py`                         |
| Model install/delete (download, path-safety, checksum)    | `models.py`                                |
| Daemon-thread lifecycle                                   | `runtime.py`                               |

## Release

This repo is a public mirror of the plugin developed in the StableArt monorepo. Edits land in the
monorepo; tagging a release mirrors the subtree here and publishes to the Comfy Registry. See
`PUBLISHING.md`.

# StableArt Job Node (ComfyUI plugin)

A ComfyUI custom node that turns a ComfyUI install into a **StableArt GPU worker**. It connects to
the StableArt node broker over WebSocket and runs image-generation and `ai-caption` jobs against
the ComfyUI it lives in — so an operator who already runs ComfyUI just adds this node instead of
running a separate worker process.

It registers no graph nodes; it's a background service that starts with ComfyUI, connects out to
the broker, and reports results. Your models, configs, and other custom nodes are untouched.

> **Operator-only.** Node registration is restricted to `owner`/`admin` accounts, so the API key
> you configure must belong to a StableArt operator account.

## Install

### ComfyUI-Manager (recommended)

In your existing ComfyUI: **Manager → Custom Nodes Manager → search "StableArt Job Node" →
Install** (or **Install via Git URL** with this repo's URL). Manager copies it into `custom_nodes/`
and installs `requirements.txt` for you. Then [configure](#configure) and restart ComfyUI.

### Manual

```bash
git clone <this-repo-url> /path/to/ComfyUI/custom_nodes/stableart-comfyui-node
/path/to/ComfyUI/python -m pip install -r /path/to/ComfyUI/custom_nodes/stableart-comfyui-node/requirements.txt
```

Use the **same** Python that launches ComfyUI (portable: `python_embeded\python.exe`; venv: that
venv's python). Then configure and restart.

## Configure

Three ways, in precedence order (highest first):

1. **Environment variables** — set before launching ComfyUI (best for Docker/systemd).
2. **`config.toml`** — copy `config.example.toml` to `config.toml` in this folder.
3. **ComfyUI Settings panel** — **Settings → StableArt Job Node** (broker URL, API key, node
   name). Persists in ComfyUI's `comfy.settings.json`; the plugin reads it at startup. **Restart
   ComfyUI to apply** a change (the broker connection is made once at startup).

Only the **API key** is required — set it (via any of the above) or the plugin logs a warning and
stays idle (ComfyUI still starts normally). The broker URL defaults to `broker.stableart.io`.

| Env var                    | `config.toml` key       | Required | Default                       | Meaning                                               |
| -------------------------- | ----------------------- | -------- | ----------------------------- | ----------------------------------------------------- |
| `GATEWAY_API_KEY`          | `api_key`               | Yes      | —                             | Operator (owner/admin) API key                        |
| `NODE_BROKER_URL`          | `broker_base_url`       | No       | `https://broker.stableart.io` | Broker base URL (`http(s)`, auto-upgraded to `ws(s)`) |
| `NODE_NAME`                | `node_name`             | No       | `ComfyUI Plugin Node`         | Label shown in the dashboard                          |
| `COMFYUI_POLL_INTERVAL_MS` | `poll_interval_ms`      | No       | `1000`                        | `/history` poll cadence                               |
| `HEARTBEAT_INTERVAL_MS`    | `heartbeat_interval_ms` | No       | `15000`                       | Heartbeat cadence                                     |
| `NODE_JOB_TIMEOUT_MS`      | `job_timeout_ms`        | No       | `120000`                      | Per-job timeout when broker omits one                 |
| `NODE_GPU_NAME`            | `gpu_name`              | No       | `nvidia-smi`                  | Override auto-detected GPU name                       |
| `NODE_ID_FILE`             | `node_id_file`          | No       | `data/node-id`                | Where the assigned node id persists                   |

The assigned `nodeId` is written to `data/node-id` and re-sent on reconnect, so the node keeps the
same identity across ComfyUI restarts. Keep that file.

## Running in Docker

If your ComfyUI runs in a container, install the node into **your** ComfyUI image/volume — don't
replace it. Either install via Manager inside the container, or bind-mount this folder into your
ComfyUI's `custom_nodes/` and make sure `websocket-client` + `requests` are in that image's Python
(e.g. a one-line `FROM your-comfyui-image` layer that `pip install`s `requirements.txt`). Mount a
volume for `data/` (or set `NODE_ID_FILE`) so the node id persists. Pass config via env. The
container only dials **out** to the broker — no inbound ports.

## How it works

On ComfyUI startup the plugin spawns a background daemon thread (never blocking ComfyUI). It waits
for the local ComfyUI HTTP server, connects to the broker (`/nodes/connect`, `x-api-key` header),
persists the assigned `nodeId`, heartbeats, and reports installed models. For each job it builds a
ComfyUI workflow, submits it (`POST /prompt`), polls `GET /history/:id`, downloads the result
(`GET /view`), and returns it; `ai-caption` uploads its input via `POST /upload/image` and returns
caption text. It reconnects with capped exponential backoff if the broker drops. Only ComfyUI's
stable HTTP API is used.

### Shared queue caveat

The plugin submits to ComfyUI's real prompt queue, so platform jobs interleave with interactive
use of the same ComfyUI. For a GPU box used both ways, run a dedicated ComfyUI instance for
platform jobs.

## Development

```bash
python -m venv .venv && ./.venv/Scripts/python -m pip install -e ".[dev]"   # Windows paths
pytest        # unit tests (pure logic; no GPU or broker needed)
ruff check .  # lint
```

See [AGENTS.md](./AGENTS.md) for the module map. This repo is mirrored from the StableArt platform
monorepo; see `PUBLISHING.md` for the release pipeline.

## License

MIT — see [LICENSE](./LICENSE).

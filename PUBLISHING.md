# Publishing the node

This node is **developed in the StableArt monorepo** (`apps/stableart-comfyui-node`, the source of
truth — it co-evolves with the broker protocol) and **mirrored to a dedicated public repo** that
is published to the **Comfy Registry** so operators can install it from ComfyUI-Manager. This file
is the one-time setup checklist for that pipeline.

## Flow

```
edit here (monorepo)  ──tag v*──►  mirror workflow  ──►  public repo (main)
                                                              │ pyproject version changed
                                                              ▼
                                                    publish-comfy-registry workflow
                                                              ▼
                                                       Comfy Registry  ──►  ComfyUI-Manager
```

- `.github/workflows/mirror-stableart-comfyui-node.yml` (monorepo root) pushes the
  `apps/stableart-comfyui-node` subtree to the public repo's `main` on every `v*` tag.
- `.github/workflows/publish-comfy-registry.yml` (this dir → becomes the public repo's root
  workflow) publishes to the registry when `pyproject.toml`'s version changes.

## One-time setup

Public repo: **`tomny-dev/stableart-comfyui-node`** (created).

1. **Comfy Registry publisher**: `stableart` (created at <https://registry.comfy.org>). Generate a
   publisher API key for CI. `[tool.comfy] PublisherId = "stableart"` is already set; the registry
   node id comes from `[project] name` (`stableart-comfyui-node`) and the Manager display name from
   `[tool.comfy] DisplayName` ("StableArt Job Node"), giving
   `registry.comfy.org/nodes/stableart-comfyui-node`.
2. **License:** MIT — `LICENSE` in this dir, `[project] license = "MIT"`. (Update the copyright
   holder in `LICENSE` if "StableArt" isn't the right entity.)
3. **Monorepo** → Settings → Secrets → Actions: secret `PLUGIN_MIRROR_TOKEN` = a PAT with `repo`
   write on the public repo. (The target repo `tomny-dev/stableart-comfyui-node` is hardcoded in
   `.github/workflows/mirror-stableart-comfyui-node.yml`.)
4. **Public repo** (`tomny-dev/stableart-comfyui-node`) → secret `REGISTRY_ACCESS_TOKEN` = the
   registry publisher API key.

## Releasing a new version

1. Bump `version` in `apps/stableart-comfyui-node/pyproject.toml`.
2. Commit and push a `vX.Y.Z` tag on the monorepo.
3. The mirror workflow updates the public repo; the publish workflow ships the new version to the
   registry; ComfyUI-Manager picks it up.

## Operator install (what end users do)

In their **existing** ComfyUI: ComfyUI-Manager → install **StableArt Job Node** (registry), or
"Install via Git URL" against the public repo. Manager drops it into `custom_nodes/` and installs
`requirements.txt` — their models, configs, and other nodes are untouched. Then configure the
broker URL + operator API key (env, `config.toml`, or the Settings panel) and restart ComfyUI.

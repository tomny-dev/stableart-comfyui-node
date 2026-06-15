"""Load and patch ComfyUI API-format workflow graphs.

Ports ``loadWorkflow`` / ``patchWorkflow`` from ``node.ts``. A workflow graph is
a dict keyed by node-id strings, each ``{"class_type": str, "inputs": dict}``.
The bundled graphs live in ``workflows/data/`` and are read via
``importlib.resources`` so they resolve regardless of where ComfyUI-Manager
installs the package.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

WorkflowGraph = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class WorkflowPatch:
    node_id: str
    input_key: str
    value: Any


_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def load_workflow(name: str) -> WorkflowGraph:
    """Load a bundled workflow JSON by name (without extension).

    The bundled workflow names are strictly controlled, so ``name`` must match a
    plain ``[A-Za-z0-9_-]`` pattern. This rejects any path separator (``os.path``
    helpers don't treat a backslash as a separator on POSIX) before it is joined
    to the data dir.
    """
    if not _WORKFLOW_NAME_RE.match(name):
        raise ValueError(f"invalid workflow name: {name!r}")
    data_pkg = resources.files("comfyui_job_plugin.workflows") / "data"
    raw = (data_pkg / f"{name}.json").read_text(encoding="utf8")
    return json.loads(raw)


def patch_workflow(workflow: WorkflowGraph, patches: list[WorkflowPatch]) -> WorkflowGraph:
    """Return a deep copy of ``workflow`` with the given input patches applied.

    Raises ``KeyError`` (via a clear message) if a patch targets a missing node,
    matching the TS behavior of throwing on an unknown patch target.
    """
    result: WorkflowGraph = copy.deepcopy(workflow)
    for patch in patches:
        if patch.node_id not in result:
            raise ValueError(f'Workflow patch target node "{patch.node_id}" not found')
        node = result[patch.node_id]
        if not isinstance(node.get("inputs"), dict):
            raise ValueError(
                f'Workflow patch target node "{patch.node_id}" has no inputs dictionary'
            )
        node["inputs"][patch.input_key] = patch.value
    return result

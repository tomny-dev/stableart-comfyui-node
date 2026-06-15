"""Checkpoint filename fuzzy-matching. Ports ``resolveCheckpoint`` from ``node.ts``."""

from __future__ import annotations

import os

from ..logging_util import log


def _basename(path: str) -> str:
    # Normalize Windows separators first: a job submitted from a Windows client
    # may carry "IL\\nova.safetensors", and os.path.basename on a Linux node
    # would not treat the backslash as a separator.
    base = os.path.basename(path.replace("\\", "/"))
    stem, _ext = os.path.splitext(base)
    return stem.lower()


def resolve_checkpoint(requested: str, installed: list[str]) -> str:
    """Resolve a requested checkpoint name against the installed list.

    Tries, in order: exact match, exact basename match (directory + extension
    stripped from both sides), then a basename prefix match. Falls back to the
    original value so ComfyUI surfaces the error itself.
    """
    if requested in installed:
        return requested

    needle_base = _basename(requested)

    for model in installed:
        if _basename(model) == needle_base:
            return model

    # Guard the prefix match: an empty needle (e.g. requested name that basenames
    # to "") would startswith-match the first installed model.
    if needle_base:
        for model in installed:
            if _basename(model).startswith(needle_base):
                log(f'Checkpoint fuzzy match: "{requested}" resolved to "{model}"')
                return model

    log(f'Checkpoint not found: "{requested}" (installed: {", ".join(installed)})')
    return requested

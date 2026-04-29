"""Runtime-settable configuration with file persistence.

Phase 1 froze its configuration in a ``Config`` dataclass loaded once at
import. That's right for paths and credentials, but wrong for the knobs
a user wants to tweak without restarting the process: max attempts,
exploration constant, cross-verify, auto-scan threshold, similarity-K.

This module layers a small JSON file (default ``data/settings.json``)
on top of ``CONFIG``: code calls :func:`get` (with the env-loaded value
as the implicit default) and :func:`set_many` to update keys at runtime.
The file is rewritten atomically on every change.

Keys are intentionally a fixed allow-list so the API can validate
client requests without a schema framework.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import CONFIG


# Allow-list of settable keys, with their JSON Schema-ish type tag.
# Anything not in this map is rejected by :func:`set_many`.
SETTABLE_KEYS: dict[str, str] = {
    "max_attempts":          "int>=1",
    "learner_exploration":   "float>=0",
    "cross_verify":          "bool",
    "similarity_threshold":  "float[0,1]",
    "similar_top_k":         "int>=1",
    "tool_timeout_s":        "float>0",
    "auto_scan_every_n":     "int>=0",
    "ollama_enabled":        "bool",
    "ollama_model":          "str",
    # Phase 9: rewrite-based search
    "enable_rewriting":      "bool",
    "max_rewrite_attempts":  "int>=0",
}


def _settings_path() -> Path:
    return Path(os.getenv("PRU_SETTINGS_PATH",
                          str(CONFIG.db_path.parent / "settings.json")))


_LOCK = threading.RLock()
_OVERRIDES: dict[str, Any] | None = None


def _defaults() -> dict[str, Any]:
    """The values baked into ``CONFIG`` at import. We keep them separate
    from the overrides so a user can always reset to "factory" values."""
    return {
        "max_attempts":         CONFIG.max_attempts,
        "learner_exploration":  CONFIG.learner_exploration,
        "cross_verify":         CONFIG.cross_verify,
        "similarity_threshold": CONFIG.similarity_threshold,
        "similar_top_k":        CONFIG.similar_top_k,
        "tool_timeout_s":       CONFIG.tool_timeout_s,
        "auto_scan_every_n":    int(os.getenv("PRU_AUTO_SCAN_EVERY_N", "0")),
        "ollama_enabled":       CONFIG.ollama_enabled,
        "ollama_model":         CONFIG.ollama_model,
        "enable_rewriting":     os.getenv("PRU_ENABLE_REWRITING", "true").lower() in {"1", "true", "yes"},
        "max_rewrite_attempts": int(os.getenv("PRU_MAX_REWRITE_ATTEMPTS", "2")),
    }


def _load_overrides() -> dict[str, Any]:
    p = _settings_path()
    if not p.is_file() or p.stat().st_size == 0:
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        # Drop keys that aren't in the allow-list — easy guard against
        # someone hand-editing the file with a typo.
        return {k: v for k, v in data.items() if k in SETTABLE_KEYS}
    except Exception:
        return {}


def _ensure_loaded() -> dict[str, Any]:
    global _OVERRIDES
    if _OVERRIDES is None:
        with _LOCK:
            if _OVERRIDES is None:
                _OVERRIDES = _load_overrides()
    return _OVERRIDES


def _save() -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    data = _ensure_loaded()
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, p)


# --- Public API ------------------------------------------------------------

def get(key: str) -> Any:
    """Return the live value: override if set, otherwise the env-loaded default."""
    if key not in SETTABLE_KEYS:
        raise KeyError(f"unknown setting: {key!r}")
    overrides = _ensure_loaded()
    if key in overrides:
        return overrides[key]
    return _defaults()[key]


def all_values() -> dict[str, Any]:
    """Snapshot of every settable key, with overrides applied. The result
    is JSON-safe and suitable for ``GET /config`` responses."""
    out = _defaults()
    out.update(_ensure_loaded())
    return out


def set_many(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a batch of overrides. Unknown keys raise
    :class:`KeyError`; bad types raise :class:`ValueError`. Returns the
    new full ``all_values()`` snapshot."""
    if not isinstance(updates, dict):
        raise TypeError("expected a dict of setting overrides")
    overrides = _ensure_loaded()
    with _LOCK:
        for k, v in updates.items():
            if k not in SETTABLE_KEYS:
                raise KeyError(f"unknown setting: {k!r}")
            overrides[k] = _coerce(k, v)
        _save()
    return all_values()


def reset(keys: list[str] | None = None) -> dict[str, Any]:
    """Drop overrides for the given keys (or all keys if ``None``).
    Returns the new ``all_values()`` snapshot."""
    overrides = _ensure_loaded()
    with _LOCK:
        if keys is None:
            overrides.clear()
        else:
            for k in keys:
                overrides.pop(k, None)
        _save()
    return all_values()


# --- Validation ------------------------------------------------------------

def _coerce(key: str, value: Any) -> Any:
    spec = SETTABLE_KEYS[key]
    if spec == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
            return value.lower() in {"true", "1"}
        raise ValueError(f"{key}: expected bool, got {value!r}")
    if spec.startswith("int"):
        try:
            iv = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: expected int, got {value!r}") from exc
        if spec == "int>=1" and iv < 1:
            raise ValueError(f"{key}: must be >= 1")
        if spec == "int>=0" and iv < 0:
            raise ValueError(f"{key}: must be >= 0")
        return iv
    if spec.startswith("float"):
        try:
            fv = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: expected float, got {value!r}") from exc
        if spec == "float>0" and fv <= 0:
            raise ValueError(f"{key}: must be > 0")
        if spec == "float>=0" and fv < 0:
            raise ValueError(f"{key}: must be >= 0")
        if spec == "float[0,1]" and not (0.0 <= fv <= 1.0):
            raise ValueError(f"{key}: must be in [0, 1]")
        return fv
    if spec == "str":
        if not isinstance(value, str):
            raise ValueError(f"{key}: expected str, got {value!r}")
        return value
    raise ValueError(f"{key}: unknown spec {spec!r}")


def reload_for_tests() -> None:
    """Test helper: forget any cached overrides so the next ``get()`` re-reads
    from disk. Call this from a fixture between cases."""
    global _OVERRIDES
    with _LOCK:
        _OVERRIDES = None

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict
import json

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore


def _coerce_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int,)):  # 0/1
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "on", "1"):  # common forms
            return True
        if s in ("false", "no", "off", "0"):
            return False
    return default


def _ensure_dict(obj: Dict[str, Any], key: str) -> Dict[str, Any]:
    val = obj.get(key)
    if not isinstance(val, dict):
        val = {}
        obj[key] = val
    return val


def _normalize_common(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # Always guarantee nested dicts present
    _ensure_dict(cfg, "channels")
    _ensure_dict(cfg, "files")
    _ensure_dict(cfg, "outbound")
    # Coerce booleans where common
    if "show_peer_messages" in cfg:
        cfg["show_peer_messages"] = _coerce_bool(cfg.get("show_peer_messages"), True)
    # Default route normalization
    dr = str(cfg.get("default_route") or "both").lower()
    cfg["default_route"] = dr if dr in ("a", "b", "both") else "both"
    # Outbound reset_on_start normalization
    out = cfg.get("outbound") or {}
    ros = str(out.get("reset_on_start") or "clear").lower()
    out["reset_on_start"] = ros if ros in ("baseline", "clear") else "clear"
    cfg["outbound"] = out
    # Files normalization: ensure inbound/outbound_dir strings exist
    files = cfg.get("files") or {}
    if not isinstance(files.get("inbound_dir"), str):
        files["inbound_dir"] = ".cccc/work/upload/inbound"
    if not isinstance(files.get("outbound_dir"), str):
        files["outbound_dir"] = ".cccc/work/upload/outbound"
    if "enabled" in files:
        files["enabled"] = _coerce_bool(files.get("enabled"), True)
    cfg["files"] = files
    return cfg


def read_config(path: Path) -> Dict[str, Any]:
    """Load a YAML (preferred) or JSON config file and normalize common keys.
    Returns an empty dict on failure.
    """
    try:
        if not path.exists():
            return _normalize_common({})
        txt = path.read_text(encoding="utf-8")
        data: Any = None
        # Try YAML first
        if yaml is not None:
            try:
                data = yaml.safe_load(txt)
            except Exception:
                data = None
        # Fallback to JSON
        if data is None:
            try:
                data = json.loads(txt)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        return _normalize_common(data)
    except Exception:
        return _normalize_common({})


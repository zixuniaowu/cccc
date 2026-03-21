"""Daemon ops for the Presentation browser-surface transport proof."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.actors import find_actor
from ...kernel.group import load_group
from .presentation_browser_runtime import (
    close_browser_surface_session,
    get_browser_surface_session_state,
    open_browser_surface_session,
)


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _validate_actor(group: Any, by: str) -> None:
    who = str(by or "").strip()
    if not who or who in {"user", "system"}:
        return
    if not isinstance(find_actor(group, who), dict):
        raise ValueError(f"unknown actor: {who}")


def handle_presentation_browser_open(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    url = str(args.get("url") or "").strip()
    width = int(args.get("width") or 1280)
    height = int(args.get("height") or 800)
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not url:
        return _error("missing_url", "missing url")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        _validate_actor(group, by)
        state = open_browser_surface_session(group_id=group.group_id, url=url, width=width, height=height)
    except Exception as exc:
        return _error("browser_surface_open_failed", str(exc))
    return DaemonResponse(ok=True, result={"group_id": group.group_id, "browser_surface": state})


def handle_presentation_browser_info(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    state = get_browser_surface_session_state(group_id=group.group_id)
    return DaemonResponse(ok=True, result={"group_id": group.group_id, "browser_surface": state})


def handle_presentation_browser_close(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        _validate_actor(group, by)
        result = close_browser_surface_session(group_id=group.group_id)
    except Exception as exc:
        return _error("browser_surface_close_failed", str(exc))
    return DaemonResponse(ok=True, result={"group_id": group.group_id, **result})


def try_handle_presentation_browser_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    if op == "presentation_browser_open":
        return handle_presentation_browser_open(args)
    if op == "presentation_browser_info":
        return handle_presentation_browser_info(args)
    if op == "presentation_browser_close":
        return handle_presentation_browser_close(args)
    return None

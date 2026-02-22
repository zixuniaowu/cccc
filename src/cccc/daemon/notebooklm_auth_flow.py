from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..providers.notebooklm.health import parse_notebooklm_auth_json
from ..providers.notebooklm.compat import probe_notebooklm_vendor
from ..providers.notebooklm.errors import NotebookLMProviderError
from .group_space_store import set_space_provider_state, update_space_provider_secrets

_FLOW_LOCK = threading.Lock()
_FLOW_STATE: Dict[str, Any] = {
    "provider": "notebooklm",
    "state": "idle",
    "phase": "idle",
    "session_id": "",
    "started_at": "",
    "updated_at": "",
    "finished_at": "",
    "message": "",
    "error": {},
}
_FLOW_THREAD: Optional[threading.Thread] = None
_FLOW_CANCEL_EVENT: Optional[threading.Event] = None
_SPACE_PROVIDER_SECRET_KEY = "NOTEBOOKLM_AUTH_JSON"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _snapshot_state() -> Dict[str, Any]:
    with _FLOW_LOCK:
        out = deepcopy(_FLOW_STATE)
    return out


def _update_state(**updates: Any) -> Dict[str, Any]:
    with _FLOW_LOCK:
        _FLOW_STATE.update(updates)
        _FLOW_STATE["updated_at"] = _now_iso()
        if not str(_FLOW_STATE.get("provider") or "").strip():
            _FLOW_STATE["provider"] = "notebooklm"
        out = deepcopy(_FLOW_STATE)
    return out


def _set_failed(*, code: str, message: str) -> Dict[str, Any]:
    return _update_state(
        state="failed",
        phase="failed",
        finished_at=_now_iso(),
        message=str(message or "NotebookLM connect failed"),
        error={"code": str(code or "space_provider_auth_flow_failed"), "message": str(message or "failed")},
    )


def _set_canceled(message: str = "Connect canceled") -> Dict[str, Any]:
    return _update_state(
        state="canceled",
        phase="canceled",
        finished_at=_now_iso(),
        message=str(message or "Connect canceled"),
        error={},
    )


def _set_succeeded(message: str = "Connected") -> Dict[str, Any]:
    return _update_state(
        state="succeeded",
        phase="done",
        finished_at=_now_iso(),
        message=str(message or "Connected"),
        error={},
    )


def _auth_cookies_ready(storage_state: Dict[str, Any]) -> bool:
    cookies = storage_state.get("cookies") if isinstance(storage_state.get("cookies"), list) else []
    if not cookies:
        return False
    known_cookie_names = {"SID", "__Secure-1PSID", "__Secure-3PSID"}
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name in known_cookie_names:
            return True
    return False


def _cookies_ready_list(cookies: Any) -> bool:
    values = cookies if isinstance(cookies, list) else []
    if not values:
        return False
    known_cookie_names = {"SID", "__Secure-1PSID", "__Secure-3PSID"}
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name in known_cookie_names:
            return True
    return False


def _is_notebooklm_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        host = str(urlparse(raw).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("notebooklm.google.com")


def _cancel_requested(cancel_event: threading.Event, *, session_id: str) -> bool:
    if not cancel_event.is_set():
        return False
    _set_canceled(message=f"Connect canceled ({session_id})")
    return True


def _install_playwright_chromium() -> None:
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        stderr = str(proc.stderr or "").strip()
        stdout = str(proc.stdout or "").strip()
        detail = stderr or stdout or "playwright install chromium failed"
        raise RuntimeError(detail[:800])


def _install_playwright_package() -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "playwright>=1.40,<2",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        stderr = str(proc.stderr or "").strip()
        stdout = str(proc.stdout or "").strip()
        detail = stderr or stdout or "pip install playwright failed"
        raise RuntimeError(detail[:1000])


def _ensure_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception:
        _update_state(
            state="running",
            phase="installing_playwright",
            message="Installing Playwright (first run only)...",
            error={},
        )
        _install_playwright_package()
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception as e:
        raise RuntimeError(f"failed to initialize Playwright after auto-install: {e}") from e


def _start_browser(playwright_obj: Any) -> Any:
    # Use Playwright-managed Chromium only. This keeps auth flow deterministic
    # and avoids system browser policy/extensions that can cause unstable UI
    # behavior during interactive login.
    try:
        return playwright_obj.chromium.launch(headless=False)
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            _update_state(
                state="running",
                phase="installing_browser_runtime",
                message="Installing Chromium runtime (first run only)...",
                error={},
            )
            _install_playwright_chromium()
            return playwright_obj.chromium.launch(headless=False)
        raise RuntimeError(msg[:1600] or "unable to start Chromium") from e


def _verify_storage_state(storage_state: Dict[str, Any]) -> None:
    try:
        from ..providers.notebooklm._vendor.notebooklm.auth import (
            extract_cookies_from_storage,
            fetch_tokens,
        )
    except Exception as e:
        raise RuntimeError(f"NotebookLM vendor package unavailable: {e}") from e

    auth_json = json.dumps(storage_state, ensure_ascii=False)
    _ = parse_notebooklm_auth_json(auth_json, label=_SPACE_PROVIDER_SECRET_KEY)
    cookies = extract_cookies_from_storage(storage_state)
    _ = asyncio.run(fetch_tokens(cookies))
    compat = probe_notebooklm_vendor()
    if not bool(compat.compatible):
        raise RuntimeError(str(compat.reason or "NotebookLM vendor compatibility mismatch"))


def _persist_storage_state(storage_state: Dict[str, Any]) -> None:
    auth_json = json.dumps(storage_state, ensure_ascii=False)
    _ = update_space_provider_secrets(
        "notebooklm",
        set_vars={_SPACE_PROVIDER_SECRET_KEY: auth_json},
        unset_keys=[],
        clear=False,
    )


def _connect_worker(*, session_id: str, timeout_seconds: int, cancel_event: threading.Event) -> None:
    try:
        _update_state(
            state="running",
            phase="preparing_browser",
            message="Preparing browser...",
            error={},
        )
        try:
            sync_playwright = _ensure_sync_playwright()
        except Exception as e:
            message = f"Failed to prepare browser runtime for Google sign-in: {e}"
            _ = set_space_provider_state(
                "notebooklm",
                enabled=True,
                real_enabled=True,
                mode="degraded",
                last_error=message,
                touch_health=True,
            )
            _set_failed(
                code="space_provider_auth_flow_dependency_missing",
                message=message,
            )
            return

        # Persist real-adapter intent and apply to this daemon process.
        os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
        _ = set_space_provider_state(
            "notebooklm",
            enabled=True,
            real_enabled=True,
            mode="active",
            last_error="",
            touch_health=True,
        )

        deadline = time.time() + max(60, min(timeout_seconds, 1800))
        with sync_playwright() as pw:
            browser = _start_browser(pw)
            try:
                context = browser.new_context()
                page = context.new_page()
                next_probe_at = 0.0
                _update_state(
                    state="running",
                    phase="waiting_user_login",
                    message="Browser opened. Sign in with Google in the opened window.",
                    error={},
                )
                page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=120000)
                while True:
                    if _cancel_requested(cancel_event, session_id=session_id):
                        return
                    if time.time() >= deadline:
                        message = "Google sign-in timed out. Please retry Connect."
                        _ = set_space_provider_state(
                            "notebooklm",
                            enabled=True,
                            real_enabled=True,
                            mode="degraded",
                            last_error=message,
                            touch_health=True,
                        )
                        _set_failed(
                            code="space_provider_auth_flow_timeout",
                            message=message,
                        )
                        return
                    now_ts = time.time()
                    if now_ts < next_probe_at:
                        time.sleep(0.5)
                        continue
                    try:
                        current_url = str(page.url or "").strip()
                    except Exception:
                        current_url = ""
                    # Avoid high-frequency storage_state polling while the user
                    # is actively completing Google login pages.
                    if not _is_notebooklm_url(current_url):
                        next_probe_at = now_ts + 2.0
                        time.sleep(0.5)
                        continue
                    try:
                        cookie_list = context.cookies(["https://notebooklm.google.com", "https://accounts.google.com"])
                    except Exception:
                        cookie_list = []
                    if _cookies_ready_list(cookie_list):
                        try:
                            storage_state = context.storage_state()
                        except Exception:
                            storage_state = {}
                        if not (isinstance(storage_state, dict) and _auth_cookies_ready(storage_state)):
                            next_probe_at = now_ts + 2.0
                            time.sleep(0.5)
                            continue
                        _update_state(
                            state="running",
                            phase="verifying_session",
                            message="Verifying session and saving credential...",
                            error={},
                        )
                        try:
                            _verify_storage_state(storage_state)
                        except NotebookLMProviderError:
                            _update_state(
                                state="running",
                                phase="waiting_user_login",
                                message="Sign-in detected. Waiting for complete NotebookLM session...",
                                error={},
                            )
                            next_probe_at = now_ts + 4.0
                            time.sleep(0.5)
                            continue
                        except Exception as e:
                            msg = str(e)
                            if "vendor package unavailable" in msg or "compatibility mismatch" in msg:
                                raise
                            _update_state(
                                state="running",
                                phase="waiting_user_login",
                                message="Sign-in detected. Waiting for complete NotebookLM session...",
                                error={},
                            )
                            next_probe_at = now_ts + 4.0
                            time.sleep(0.5)
                            continue
                        _persist_storage_state(storage_state)
                        _ = set_space_provider_state(
                            "notebooklm",
                            enabled=True,
                            real_enabled=True,
                            mode="active",
                            last_error="",
                            touch_health=True,
                        )
                        _set_succeeded("Google account connected.")
                        return
                    next_probe_at = now_ts + 2.0
                    time.sleep(0.5)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        _ = set_space_provider_state(
            "notebooklm",
            enabled=True,
            real_enabled=True,
            mode="degraded",
            last_error=str(e),
            touch_health=True,
        )
        _set_failed(code="space_provider_auth_flow_failed", message=str(e))


def start_notebooklm_auth_flow(*, timeout_seconds: int = 900) -> Dict[str, Any]:
    global _FLOW_THREAD, _FLOW_CANCEL_EVENT
    with _FLOW_LOCK:
        active = _FLOW_THREAD is not None and _FLOW_THREAD.is_alive()
        if active:
            return deepcopy(_FLOW_STATE)
        session_id = f"nbl_auth_{secrets.token_hex(6)}"
        cancel_event = threading.Event()
        _FLOW_CANCEL_EVENT = cancel_event
        _FLOW_THREAD = threading.Thread(
            target=_connect_worker,
            name="cccc-notebooklm-auth",
            kwargs={
                "session_id": session_id,
                "timeout_seconds": int(timeout_seconds or 900),
                "cancel_event": cancel_event,
            },
            daemon=True,
        )
        _FLOW_STATE.update(
            {
                "provider": "notebooklm",
                "state": "running",
                "phase": "starting",
                "session_id": session_id,
                "started_at": _now_iso(),
                "updated_at": _now_iso(),
                "finished_at": "",
                "message": "Starting Google connect flow...",
                "error": {},
            }
        )
        _FLOW_THREAD.start()
        return deepcopy(_FLOW_STATE)


def get_notebooklm_auth_flow_status() -> Dict[str, Any]:
    return _snapshot_state()


def cancel_notebooklm_auth_flow() -> Dict[str, Any]:
    with _FLOW_LOCK:
        cancel_event = _FLOW_CANCEL_EVENT
        running = _FLOW_THREAD is not None and _FLOW_THREAD.is_alive()
    if running and isinstance(cancel_event, threading.Event):
        cancel_event.set()
        return _update_state(
            state="running",
            phase="canceling",
            message="Cancel requested...",
        )
    return _snapshot_state()

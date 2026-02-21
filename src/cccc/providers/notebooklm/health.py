from __future__ import annotations

import json
import os
from typing import Any, Dict

from .compat import probe_notebooklm_vendor
from .errors import NotebookLMProviderError


def _truthy_env(name: str) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def notebooklm_real_enabled() -> bool:
    return _truthy_env("CCCC_NOTEBOOKLM_REAL")


def validate_notebooklm_auth_json() -> Dict[str, Any]:
    raw = str(os.environ.get("CCCC_NOTEBOOKLM_AUTH_JSON") or "").strip()
    if not raw:
        raise NotebookLMProviderError(
            code="space_provider_not_configured",
            message="missing CCCC_NOTEBOOKLM_AUTH_JSON",
            transient=False,
            degrade_provider=True,
        )
    try:
        payload = json.loads(raw)
    except Exception as e:
        raise NotebookLMProviderError(
            code="space_provider_auth_invalid",
            message=f"invalid CCCC_NOTEBOOKLM_AUTH_JSON: {e}",
            transient=False,
            degrade_provider=True,
        ) from e
    if not isinstance(payload, dict):
        raise NotebookLMProviderError(
            code="space_provider_auth_invalid",
            message="CCCC_NOTEBOOKLM_AUTH_JSON must be a JSON object",
            transient=False,
            degrade_provider=True,
        )
    cookies = payload.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise NotebookLMProviderError(
            code="space_provider_auth_invalid",
            message="CCCC_NOTEBOOKLM_AUTH_JSON missing cookies array",
            transient=False,
            degrade_provider=True,
        )
    return payload


def notebooklm_health_check() -> Dict[str, Any]:
    if not notebooklm_real_enabled():
        raise NotebookLMProviderError(
            code="space_provider_not_configured",
            message="NotebookLM real adapter is disabled (set CCCC_NOTEBOOKLM_REAL=1 to enable)",
            transient=False,
            degrade_provider=True,
        )
    _ = validate_notebooklm_auth_json()
    compat = probe_notebooklm_vendor()
    if not compat.compatible:
        raise NotebookLMProviderError(
            code="space_provider_compat_mismatch",
            message=compat.reason,
            transient=False,
            degrade_provider=True,
        )
    return {
        "provider": "notebooklm",
        "enabled": True,
        "compatible": True,
        "reason": "ok",
    }


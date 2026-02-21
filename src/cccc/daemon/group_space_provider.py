from __future__ import annotations

import os
from typing import Any, Dict

from ..providers.notebooklm.adapter import get_notebooklm_adapter
from ..providers.notebooklm.errors import NotebookLMProviderError
from ..providers.notebooklm.health import notebooklm_real_enabled


class SpaceProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        transient: bool = False,
        degrade_provider: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "space_upstream_error")
        self.transient = bool(transient)
        self.degrade_provider = bool(degrade_provider)


def _truthy_env(name: str) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _notebooklm_stub_enabled() -> bool:
    return _truthy_env("CCCC_NOTEBOOKLM_STUB")


def _notebooklm_ingest(*, remote_space_id: str, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        try:
            return adapter.ingest(
                remote_space_id=remote_space_id,
                kind=kind,
                payload=payload,
            )
        except NotebookLMProviderError as e:
            raise SpaceProviderError(
                e.code,
                str(e),
                transient=bool(e.transient),
                degrade_provider=bool(e.degrade_provider),
            ) from e
    if not _notebooklm_stub_enabled():
        raise SpaceProviderError(
            "space_provider_disabled",
            "notebooklm provider adapter is not configured in this build",
            transient=False,
            degrade_provider=True,
        )
    return {
        "provider": "notebooklm",
        "remote_space_id": str(remote_space_id or ""),
        "kind": str(kind or ""),
        "accepted": True,
        "stub": True,
        "payload_keys": sorted(payload.keys()),
    }


def _notebooklm_query(*, remote_space_id: str, query: str, options: Dict[str, Any]) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        try:
            return adapter.query(
                remote_space_id=remote_space_id,
                query=query,
                options=options,
            )
        except NotebookLMProviderError as e:
            raise SpaceProviderError(
                e.code,
                str(e),
                transient=bool(e.transient),
                degrade_provider=bool(e.degrade_provider),
            ) from e
    if not _notebooklm_stub_enabled():
        raise SpaceProviderError(
            "space_provider_disabled",
            "notebooklm provider adapter is not configured in this build",
            transient=False,
            degrade_provider=True,
        )
    prompt = str(query or "").strip()
    answer = f"[NotebookLM stub] {prompt}" if prompt else "[NotebookLM stub] empty query"
    return {
        "answer": answer,
        "references": [],
        "remote_space_id": str(remote_space_id or ""),
        "options": dict(options or {}),
        "stub": True,
    }


def provider_ingest(
    provider: str,
    *,
    remote_space_id: str,
    kind: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_ingest(remote_space_id=remote_space_id, kind=kind, payload=payload)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")


def provider_query(
    provider: str,
    *,
    remote_space_id: str,
    query: str,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_query(remote_space_id=remote_space_id, query=query, options=options)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")

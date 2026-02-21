from __future__ import annotations

import os
import inspect
import secrets
from typing import Any, Dict

from ..providers.notebooklm.adapter import get_notebooklm_adapter
from ..providers.notebooklm.errors import NotebookLMProviderError
from ..providers.notebooklm.health import notebooklm_real_enabled
from .group_space_store import load_space_provider_secrets

_NOTEBOOKLM_AUTH_KEY = "NOTEBOOKLM_AUTH_JSON"


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


def _resolve_notebooklm_auth_json() -> str:
    raw_env = str(os.environ.get("CCCC_NOTEBOOKLM_AUTH_JSON") or "").strip()
    if raw_env:
        return raw_env
    try:
        secrets_map = load_space_provider_secrets("notebooklm")
    except Exception:
        return ""
    return str(secrets_map.get(_NOTEBOOKLM_AUTH_KEY) or "").strip()


def _notebooklm_ingest(*, remote_space_id: str, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            ingest_fn = adapter.ingest
            supports_auth_override = False
            try:
                params = inspect.signature(ingest_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return ingest_fn(
                    remote_space_id=remote_space_id,
                    kind=kind,
                    payload=payload,
                    auth_json_raw=auth_json_raw,
                )
            return ingest_fn(
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
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            query_fn = adapter.query
            supports_auth_override = False
            try:
                params = inspect.signature(query_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return query_fn(
                    remote_space_id=remote_space_id,
                    query=query,
                    options=options,
                    auth_json_raw=auth_json_raw,
                )
            return query_fn(
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


def _notebooklm_create_space(*, title: str) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            create_fn = adapter.create_notebook
            supports_auth_override = False
            try:
                params = inspect.signature(create_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return create_fn(
                    title=title,
                    auth_json_raw=auth_json_raw,
                )
            return create_fn(title=title)
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
        "remote_space_id": f"nb_stub_{secrets.token_hex(8)}",
        "title": str(title or "CCCC Space"),
        "created": True,
        "stub": True,
    }


def _notebooklm_list_sources(*, remote_space_id: str) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            list_fn = adapter.list_sources
            supports_auth_override = False
            try:
                params = inspect.signature(list_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return list_fn(
                    remote_space_id=remote_space_id,
                    auth_json_raw=auth_json_raw,
                )
            return list_fn(remote_space_id=remote_space_id)
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
        "sources": [],
        "stub": True,
    }


def _notebooklm_add_file_source(*, remote_space_id: str, file_path: str) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            add_fn = adapter.add_file_source
            supports_auth_override = False
            try:
                params = inspect.signature(add_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return add_fn(
                    remote_space_id=remote_space_id,
                    file_path=file_path,
                    auth_json_raw=auth_json_raw,
                )
            return add_fn(
                remote_space_id=remote_space_id,
                file_path=file_path,
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
        "source_id": f"src_stub_{secrets.token_hex(8)}",
        "title": os.path.basename(str(file_path or "")),
        "accepted": True,
        "stub": True,
    }


def _notebooklm_delete_source(*, remote_space_id: str, source_id: str) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            delete_fn = adapter.delete_source
            supports_auth_override = False
            try:
                params = inspect.signature(delete_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return delete_fn(
                    remote_space_id=remote_space_id,
                    source_id=source_id,
                    auth_json_raw=auth_json_raw,
                )
            return delete_fn(
                remote_space_id=remote_space_id,
                source_id=source_id,
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
        "source_id": str(source_id or ""),
        "deleted": True,
        "stub": True,
    }


def _notebooklm_rename_source(*, remote_space_id: str, source_id: str, new_title: str) -> Dict[str, Any]:
    if notebooklm_real_enabled():
        adapter = get_notebooklm_adapter()
        auth_json_raw = _resolve_notebooklm_auth_json()
        try:
            rename_fn = adapter.rename_source
            supports_auth_override = False
            try:
                params = inspect.signature(rename_fn).parameters
                supports_auth_override = "auth_json_raw" in params
            except Exception:
                supports_auth_override = False
            if supports_auth_override:
                return rename_fn(
                    remote_space_id=remote_space_id,
                    source_id=source_id,
                    new_title=new_title,
                    auth_json_raw=auth_json_raw,
                )
            return rename_fn(
                remote_space_id=remote_space_id,
                source_id=source_id,
                new_title=new_title,
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
        "source_id": str(source_id or ""),
        "title": str(new_title or ""),
        "renamed": True,
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


def provider_create_space(
    provider: str,
    *,
    title: str,
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_create_space(title=title)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")


def provider_list_sources(
    provider: str,
    *,
    remote_space_id: str,
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_list_sources(remote_space_id=remote_space_id)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")


def provider_add_file_source(
    provider: str,
    *,
    remote_space_id: str,
    file_path: str,
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_add_file_source(remote_space_id=remote_space_id, file_path=file_path)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")


def provider_delete_source(
    provider: str,
    *,
    remote_space_id: str,
    source_id: str,
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_delete_source(remote_space_id=remote_space_id, source_id=source_id)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")


def provider_rename_source(
    provider: str,
    *,
    remote_space_id: str,
    source_id: str,
    new_title: str,
) -> Dict[str, Any]:
    pid = str(provider or "").strip() or "notebooklm"
    if pid == "notebooklm":
        return _notebooklm_rename_source(remote_space_id=remote_space_id, source_id=source_id, new_title=new_title)
    raise SpaceProviderError("space_job_invalid", f"unsupported provider: {pid}")

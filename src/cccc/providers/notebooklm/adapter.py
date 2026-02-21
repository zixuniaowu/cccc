from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import NotebookLMProviderError
from .health import notebooklm_health_check, validate_notebooklm_auth_json


class NotebookLMAdapter:
    """Minimal provider adapter contract for Group Space M2 wiring.

    M2 scope:
    - validate enable/auth/compat preconditions
    - perform real ingest/query calls via vendored notebooklm-py boundary
    - preserve strict error typing for daemon-level degrade semantics
    """

    provider_id = "notebooklm"

    def health_check(self, auth_json_raw: str | None = None) -> Dict[str, Any]:
        return notebooklm_health_check(auth_json_raw=auth_json_raw)

    def ingest(
        self,
        *,
        remote_space_id: str,
        kind: str,
        payload: Dict[str, Any],
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )

        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        normalized_kind = str(kind or "context_sync").strip() or "context_sync"
        try:
            return _run_coroutine_sync(
                _ingest_async(
                    notebook_id=notebook_id,
                    kind=normalized_kind,
                    payload=dict(payload or {}),
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def query(
        self,
        *,
        remote_space_id: str,
        query: str,
        options: Dict[str, Any],
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )
        question = str(query or "").strip()
        if not question:
            raise NotebookLMProviderError(
                code="space_job_invalid",
                message="query is required",
                transient=False,
                degrade_provider=False,
            )
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        safe_options = dict(options or {})
        try:
            return _run_coroutine_sync(
                _query_async(
                    notebook_id=notebook_id,
                    query=question,
                    options=safe_options,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def create_notebook(
        self,
        *,
        title: str,
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_title = str(title or "").strip() or "CCCC Space"
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        try:
            return _run_coroutine_sync(
                _create_notebook_async(
                    title=notebook_title,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def list_sources(
        self,
        *,
        remote_space_id: str,
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        try:
            return _run_coroutine_sync(
                _list_sources_async(
                    notebook_id=notebook_id,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def add_file_source(
        self,
        *,
        remote_space_id: str,
        file_path: str,
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )
        target = str(file_path or "").strip()
        if not target:
            raise NotebookLMProviderError(
                code="space_job_invalid",
                message="file_path is required",
                transient=False,
                degrade_provider=False,
            )
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        try:
            return _run_coroutine_sync(
                _add_file_source_async(
                    notebook_id=notebook_id,
                    file_path=target,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def delete_source(
        self,
        *,
        remote_space_id: str,
        source_id: str,
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        sid = str(source_id or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )
        if not sid:
            raise NotebookLMProviderError(
                code="space_job_invalid",
                message="source_id is required",
                transient=False,
                degrade_provider=False,
            )
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        try:
            return _run_coroutine_sync(
                _delete_source_async(
                    notebook_id=notebook_id,
                    source_id=sid,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e

    def rename_source(
        self,
        *,
        remote_space_id: str,
        source_id: str,
        new_title: str,
        auth_json_raw: str | None = None,
    ) -> Dict[str, Any]:
        self.health_check(auth_json_raw=auth_json_raw)
        notebook_id = str(remote_space_id or "").strip()
        sid = str(source_id or "").strip()
        title = str(new_title or "").strip()
        if not notebook_id:
            raise NotebookLMProviderError(
                code="space_provider_not_configured",
                message="missing remote_space_id (NotebookLM notebook id)",
                transient=False,
                degrade_provider=True,
            )
        if not sid:
            raise NotebookLMProviderError(
                code="space_job_invalid",
                message="source_id is required",
                transient=False,
                degrade_provider=False,
            )
        if not title:
            raise NotebookLMProviderError(
                code="space_job_invalid",
                message="new_title is required",
                transient=False,
                degrade_provider=False,
            )
        auth_payload = validate_notebooklm_auth_json(auth_json_raw=auth_json_raw)
        try:
            return _run_coroutine_sync(
                _rename_source_async(
                    notebook_id=notebook_id,
                    source_id=sid,
                    new_title=title,
                    auth_payload=auth_payload,
                    timeout_seconds=_read_timeout_seconds(),
                )
            )
        except Exception as e:
            raise _map_vendor_exception(e) from e


def _read_timeout_seconds() -> float:
    raw = str(os.environ.get("CCCC_NOTEBOOKLM_TIMEOUT") or "").strip()
    if not raw:
        return 30.0
    try:
        timeout = float(raw)
    except Exception:
        return 30.0
    if timeout <= 0:
        return 30.0
    return min(timeout, 300.0)


def _run_coroutine_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_holder["value"] = asyncio.run(coro)
        except BaseException as e:  # pragma: no cover - exercised through caller paths
            error_holder["error"] = e

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("value")


def _normalize_ingest_title(*, kind: str, payload: Mapping[str, Any]) -> str:
    title = str(payload.get("title") or "").strip()
    if title:
        return title[:120]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    return f"CCCC {kind} {stamp}"


def _payload_to_text(payload: Mapping[str, Any]) -> str:
    if isinstance(payload.get("content"), str) and str(payload.get("content") or "").strip():
        return str(payload.get("content"))
    if isinstance(payload.get("text"), str) and str(payload.get("text") or "").strip():
        return str(payload.get("text"))
    return json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)


def _extract_query_source_ids(options: Mapping[str, Any]) -> list[str] | None:
    raw = options.get("source_ids")
    if not isinstance(raw, list):
        return None
    source_ids: list[str] = []
    for item in raw:
        sid = str(item or "").strip()
        if sid:
            source_ids.append(sid)
    return source_ids or None


async def _build_client(*, auth_payload: Dict[str, Any], timeout_seconds: float):
    from ._vendor.notebooklm.auth import AuthTokens, extract_cookies_from_storage, fetch_tokens
    from ._vendor.notebooklm.client import NotebookLMClient

    cookies = extract_cookies_from_storage(auth_payload)
    csrf_token, session_id = await fetch_tokens(cookies)
    auth = AuthTokens(
        cookies=cookies,
        csrf_token=csrf_token,
        session_id=session_id,
    )
    return NotebookLMClient(auth, timeout=timeout_seconds)


async def _ingest_async(
    *,
    notebook_id: str,
    kind: str,
    payload: Dict[str, Any],
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    title = _normalize_ingest_title(kind=kind, payload=payload)
    url = str(payload.get("url") or "").strip()
    async with client:
        if kind == "resource_ingest" and url:
            source = await client.sources.add_url(notebook_id, url, wait=False)
            source_mode = "url"
        else:
            text = _payload_to_text(payload)
            source = await client.sources.add_text(
                notebook_id,
                title=title,
                content=text,
                wait=False,
            )
            source_mode = "text"
    return {
        "provider": "notebooklm",
        "remote_space_id": notebook_id,
        "accepted": True,
        "kind": kind,
        "source_mode": source_mode,
        "source_id": str(getattr(source, "id", "") or ""),
        "title": str(getattr(source, "title", "") or title),
    }


def _reference_to_dict(ref: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "source_id": str(getattr(ref, "source_id", "") or ""),
    }
    citation_number = getattr(ref, "citation_number", None)
    if citation_number is not None:
        try:
            data["citation_number"] = int(citation_number)
        except Exception:
            pass
    cited_text = str(getattr(ref, "cited_text", "") or "").strip()
    if cited_text:
        data["cited_text"] = cited_text
    return data


async def _query_async(
    *,
    notebook_id: str,
    query: str,
    options: Dict[str, Any],
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    source_ids = _extract_query_source_ids(options)
    async with client:
        result = await client.chat.ask(
            notebook_id,
            question=query,
            source_ids=source_ids,
        )
    refs_raw = list(getattr(result, "references", []) or [])
    references = [_reference_to_dict(ref) for ref in refs_raw]
    return {
        "answer": str(getattr(result, "answer", "") or ""),
        "references": references,
        "remote_space_id": notebook_id,
        "provider": "notebooklm",
    }


async def _create_notebook_async(
    *,
    title: str,
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    async with client:
        notebook = await client.notebooks.create(title)
    notebook_id = str(getattr(notebook, "id", "") or "").strip()
    if not notebook_id:
        raise NotebookLMProviderError(
            code="space_provider_upstream_error",
            message="notebook create returned empty notebook id",
            transient=False,
            degrade_provider=False,
        )
    return {
        "provider": "notebooklm",
        "remote_space_id": notebook_id,
        "title": str(getattr(notebook, "title", "") or title),
        "created": True,
    }


def _source_to_dict(source: Any) -> Dict[str, Any]:
    return {
        "source_id": str(getattr(source, "id", "") or ""),
        "title": str(getattr(source, "title", "") or ""),
        "url": str(getattr(source, "url", "") or ""),
        "status": int(getattr(source, "status", 0) or 0),
        "kind": str(getattr(source, "kind", "") or ""),
    }


async def _list_sources_async(
    *,
    notebook_id: str,
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    async with client:
        sources = await client.sources.list(notebook_id)
    return {
        "provider": "notebooklm",
        "remote_space_id": notebook_id,
        "sources": [_source_to_dict(source) for source in list(sources or [])],
    }


async def _add_file_source_async(
    *,
    notebook_id: str,
    file_path: str,
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    target = Path(file_path).expanduser().resolve()
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    async with client:
        source = await client.sources.add_file(notebook_id, str(target), wait=False)
    out = _source_to_dict(source)
    out["provider"] = "notebooklm"
    out["remote_space_id"] = notebook_id
    out["file_path"] = str(target)
    out["accepted"] = True
    return out


async def _delete_source_async(
    *,
    notebook_id: str,
    source_id: str,
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    async with client:
        ok = await client.sources.delete(notebook_id, source_id)
    return {
        "provider": "notebooklm",
        "remote_space_id": notebook_id,
        "source_id": source_id,
        "deleted": bool(ok),
    }


async def _rename_source_async(
    *,
    notebook_id: str,
    source_id: str,
    new_title: str,
    auth_payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    client = await _build_client(auth_payload=auth_payload, timeout_seconds=timeout_seconds)
    async with client:
        source = await client.sources.rename(notebook_id, source_id, new_title)
    out = _source_to_dict(source)
    out["provider"] = "notebooklm"
    out["remote_space_id"] = notebook_id
    out["renamed"] = True
    out["title"] = str(out.get("title") or new_title)
    return out


def _map_vendor_exception(exc: Exception) -> NotebookLMProviderError:
    if isinstance(exc, NotebookLMProviderError):
        return exc
    try:
        from ._vendor.notebooklm.exceptions import (
            AuthError,
            ClientError,
            ConfigurationError,
            NetworkError,
            RPCTimeoutError,
            RPCError,
            RateLimitError,
            ServerError,
        )
    except Exception:
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM upstream error",
            transient=True,
            degrade_provider=False,
        )

    if isinstance(exc, FileNotFoundError):
        return NotebookLMProviderError(
            code="space_provider_not_configured",
            message=str(exc) or "NotebookLM credentials not configured",
            transient=False,
            degrade_provider=True,
        )
    if isinstance(exc, (ConfigurationError, AuthError)):
        return NotebookLMProviderError(
            code="space_provider_auth_invalid",
            message=str(exc) or "NotebookLM auth invalid",
            transient=False,
            degrade_provider=True,
        )
    if isinstance(exc, ValueError):
        text = str(exc).lower()
        if "authentication expired" in text or "re-authenticate" in text:
            return NotebookLMProviderError(
                code="space_provider_auth_invalid",
                message=str(exc) or "NotebookLM auth invalid",
                transient=False,
                degrade_provider=True,
            )
        if "page structure may have changed" in text:
            return NotebookLMProviderError(
                code="space_provider_compat_mismatch",
                message=str(exc) or "NotebookLM API compatibility mismatch",
                transient=False,
                degrade_provider=True,
            )
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM upstream error",
            transient=False,
            degrade_provider=False,
        )
    if isinstance(exc, RateLimitError):
        return NotebookLMProviderError(
            code="space_provider_rate_limited",
            message=str(exc) or "NotebookLM rate limited",
            transient=True,
            degrade_provider=False,
        )
    if isinstance(exc, RPCTimeoutError):
        return NotebookLMProviderError(
            code="space_provider_timeout",
            message=str(exc) or "NotebookLM request timed out",
            transient=True,
            degrade_provider=False,
        )
    if isinstance(exc, NetworkError):
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM network error",
            transient=True,
            degrade_provider=False,
        )
    if isinstance(exc, ServerError):
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM server error",
            transient=True,
            degrade_provider=False,
        )
    if isinstance(exc, ClientError):
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM client error",
            transient=False,
            degrade_provider=False,
        )
    if isinstance(exc, RPCError):
        return NotebookLMProviderError(
            code="space_provider_upstream_error",
            message=str(exc) or "NotebookLM RPC error",
            transient=True,
            degrade_provider=False,
        )
    return NotebookLMProviderError(
        code="space_provider_upstream_error",
        message=str(exc) or "NotebookLM upstream error",
        transient=True,
        degrade_provider=False,
    )


_ADAPTER = NotebookLMAdapter()


def get_notebooklm_adapter() -> NotebookLMAdapter:
    return _ADAPTER

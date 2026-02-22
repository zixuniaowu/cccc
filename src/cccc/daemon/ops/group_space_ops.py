"""Group Space (external memory control-plane) operation handlers for daemon."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse, SpaceBinding
from ...kernel.group import load_group
from ...kernel.permissions import require_group_permission
from ..group_space_provider import SpaceProviderError, provider_create_space
from ...providers.notebooklm.errors import NotebookLMProviderError
from ...providers.notebooklm.health import notebooklm_health_check, parse_notebooklm_auth_json
from ..notebooklm_auth_flow import (
    cancel_notebooklm_auth_flow,
    get_notebooklm_auth_flow_status,
    start_notebooklm_auth_flow,
)
from ..group_space_sync import read_group_space_sync_state, sync_group_space_files
from ..group_space_projection import sync_group_space_projection
from ..group_space_runtime import execute_space_job, retry_space_job, run_space_query
from ..group_space_store import (
    cancel_space_job,
    describe_space_provider_credential_state,
    enqueue_space_job,
    get_space_binding,
    get_space_job,
    get_space_provider_state,
    list_space_bindings,
    list_space_jobs,
    load_space_provider_secrets,
    set_space_binding_unbound,
    set_space_provider_state,
    space_queue_summary,
    update_space_provider_secrets,
    upsert_space_binding,
)

_SPACE_PROVIDER_IDS = {"notebooklm"}
_SPACE_JOB_KINDS = {"context_sync", "resource_ingest"}
_SPACE_JOB_STATES = {"pending", "running", "succeeded", "failed", "canceled"}
_SPACE_JOB_ACTIONS = {"list", "retry", "cancel"}
_SPACE_SYNC_ACTIONS = {"status", "run"}
_SPACE_PROVIDER_AUTH_ACTIONS = {"status", "start", "cancel"}
_SPACE_PROVIDER_SECRET_KEYS = {"notebooklm": "NOTEBOOKLM_AUTH_JSON"}
_LOG = logging.getLogger("cccc.daemon.group_space_ops")


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _provider_or_error(raw: Any) -> str:
    provider = str(raw or "notebooklm").strip() or "notebooklm"
    if provider not in _SPACE_PROVIDER_IDS:
        raise ValueError(f"unsupported provider: {provider}")
    return provider


def _kind_or_error(raw: Any) -> str:
    kind = str(raw or "context_sync").strip() or "context_sync"
    if kind not in _SPACE_JOB_KINDS:
        raise ValueError(f"invalid kind: {kind}")
    return kind


def _action_or_error(raw: Any) -> str:
    action = str(raw or "list").strip() or "list"
    if action not in _SPACE_JOB_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    return action


def _sync_action_or_error(raw: Any) -> str:
    action = str(raw or "status").strip() or "status"
    if action not in _SPACE_SYNC_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    return action


def _provider_auth_action_or_error(raw: Any) -> str:
    action = str(raw or "status").strip() or "status"
    if action not in _SPACE_PROVIDER_AUTH_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    return action


def _require_group(group_id: str):
    gid = str(group_id or "").strip()
    if not gid:
        raise ValueError("missing_group_id")
    group = load_group(gid)
    if group is None:
        raise LookupError(f"group not found: {gid}")
    return group


def _default_binding(group_id: str, provider: str) -> Dict[str, Any]:
    return SpaceBinding(
        group_id=str(group_id or "").strip(),
        provider=provider,
        remote_space_id="",
        bound_by="",
        status="unbound",
    ).model_dump(exclude_none=True)


def _assert_write_permission(group: Any, *, by: str) -> None:
    require_group_permission(group, by=str(by or "user").strip(), action="group.update")


def _is_user_writer(by: str) -> bool:
    who = str(by or "").strip()
    return not who or who == "user"


def _provider_secret_key(provider: str) -> str:
    key = _SPACE_PROVIDER_SECRET_KEYS.get(str(provider or "").strip())
    if not key:
        raise ValueError(f"unsupported provider: {provider}")
    return key


def _resolve_auth_json(provider: str) -> str:
    pid = _provider_or_error(provider)
    key = _provider_secret_key(pid)
    if pid == "notebooklm":
        import os

        raw_env = str(os.environ.get("CCCC_NOTEBOOKLM_AUTH_JSON") or "").strip()
        if raw_env:
            return raw_env
    secrets_map = load_space_provider_secrets(pid)
    return str(secrets_map.get(key) or "").strip()


def _truthy_env(name: str) -> bool:
    import os

    value = str(os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _provider_runtime_readiness(provider: str) -> Dict[str, Any]:
    pid = _provider_or_error(provider)
    if pid != "notebooklm":
        return {"write_ready": False, "reason": "unsupported_provider"}
    provider_state = get_space_provider_state(pid)
    real_enabled = bool(provider_state.get("real_enabled"))
    stub_enabled = bool(_truthy_env("CCCC_NOTEBOOKLM_STUB"))
    auth_configured = False
    credential_read_error = ""
    try:
        auth_configured = bool(_resolve_auth_json(pid))
    except Exception as e:
        credential_read_error = str(e)
        _LOG.warning("group-space credential read failed provider=%s: %s", pid, credential_read_error)
    write_ready = (real_enabled and auth_configured) or ((not real_enabled) and stub_enabled)
    reason = "ok"
    if credential_read_error:
        reason = "credential_read_failed"
        write_ready = False
    if not write_ready:
        if real_enabled and not auth_configured:
            reason = "missing_auth"
        elif (not real_enabled) and (not stub_enabled):
            reason = "real_disabled_and_stub_disabled"
        else:
            reason = "not_ready"
    return {
        "real_adapter_enabled": real_enabled,
        "stub_adapter_enabled": stub_enabled,
        "auth_configured": auth_configured,
        "write_ready": write_ready,
        "readiness_reason": reason,
        "credential_read_error": credential_read_error or None,
    }


def _build_provider_credential_status(provider: str) -> Dict[str, Any]:
    pid = _provider_or_error(provider)
    key = _provider_secret_key(pid)
    base = describe_space_provider_credential_state(pid, key=key)
    auth_json = _resolve_auth_json(pid)
    env_configured = False
    store_configured = bool(base.get("store_configured"))
    source = "none"
    if pid == "notebooklm":
        import os

        env_configured = bool(str(os.environ.get("CCCC_NOTEBOOKLM_AUTH_JSON") or "").strip())
    if env_configured:
        source = "env"
    elif store_configured:
        source = "store"
    out = dict(base)
    out["configured"] = bool(auth_json)
    out["source"] = source
    out["env_configured"] = env_configured
    out["store_configured"] = store_configured
    if bool(auth_json):
        if source == "env":
            out["masked_value"] = "EN******ON"
        elif not str(out.get("masked_value") or "").strip():
            out["masked_value"] = "ST******ED"
    else:
        out["masked_value"] = None
    return out


def _sync_projection_best_effort(group_id: str, provider: str) -> None:
    try:
        _ = sync_group_space_projection(group_id, provider=provider)
    except Exception as e:
        _LOG.warning("group-space projection sync failed group=%s provider=%s: %s", group_id, provider, e)


def handle_group_space_status(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    provider_raw = args.get("provider")
    try:
        group = _require_group(group_id)
        provider = _provider_or_error(provider_raw)
        provider_state = get_space_provider_state(provider)
        provider_state.update(_provider_runtime_readiness(provider))
        binding = get_space_binding(group.group_id, provider=provider) or _default_binding(group.group_id, provider)
        summary = space_queue_summary(group_id=group.group_id, provider=provider)
        sync_state = read_group_space_sync_state(group.group_id)
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider_state,
                "binding": binding,
                "queue_summary": summary,
                "sync": sync_state,
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if "permission denied" in message.lower():
            return _error("space_permission_denied", message)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("space_job_invalid", message)
    except Exception as e:
        return _error("group_space_status_failed", str(e))


def handle_group_space_bind(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    provider_raw = args.get("provider")
    action = str(args.get("action") or "bind").strip().lower() or "bind"
    remote_space_id = str(args.get("remote_space_id") or "").strip()
    if action not in {"bind", "unbind"}:
        return _error("space_job_invalid", "action must be bind|unbind")
    try:
        group = _require_group(group_id)
        _assert_write_permission(group, by=by)
        provider = _provider_or_error(provider_raw)
        sync_result: Optional[Dict[str, Any]] = None
        if action == "bind":
            if not remote_space_id:
                try:
                    created = provider_create_space(
                        provider,
                        title=f"CCCC {str(getattr(group, 'title', '') or group.group_id)} Space",
                    )
                    remote_space_id = str(created.get("remote_space_id") or "").strip()
                    if not remote_space_id:
                        return _error("space_provider_upstream_error", "provider create_space returned empty remote_space_id")
                except SpaceProviderError as e:
                    return _error(str(e.code or "space_provider_upstream_error"), str(e))
            binding = upsert_space_binding(
                group.group_id,
                provider=provider,
                remote_space_id=remote_space_id,
                by=by,
                status="bound",
            )
            provider_state = set_space_provider_state(
                provider,
                enabled=True,
                mode="degraded",
                last_error="synchronizing group space after bind",
                touch_health=True,
            )
            try:
                sync_result = sync_group_space_files(group.group_id, provider=provider, force=True)
            except Exception as e:
                sync_result = {"ok": False, "code": "space_sync_failed", "message": str(e)}
            if isinstance(sync_result, dict) and bool(sync_result.get("ok")):
                provider_state = set_space_provider_state(
                    provider,
                    enabled=True,
                    mode="active",
                    last_error="",
                    touch_health=True,
                )
            else:
                last_error = str((sync_result or {}).get("message") or "group space sync failed")
                provider_state = set_space_provider_state(
                    provider,
                    enabled=True,
                    mode="degraded",
                    last_error=last_error,
                    touch_health=True,
                )
        else:
            binding = set_space_binding_unbound(group.group_id, provider=provider, by=by)
            has_any_bound = any(
                str(item.get("status") or "") == "bound" and str(item.get("remote_space_id") or "").strip()
                for item in list_space_bindings(provider)
            )
            if has_any_bound:
                provider_state = get_space_provider_state(provider)
            else:
                provider_state = set_space_provider_state(
                    provider,
                    enabled=False,
                    mode="disabled",
                    last_error="",
                    touch_health=True,
                )
        summary = space_queue_summary(group_id=group.group_id, provider=provider)
        _sync_projection_best_effort(group.group_id, provider)
        sync_state = read_group_space_sync_state(group.group_id)
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider_state,
                "binding": binding,
                "queue_summary": summary,
                "sync": sync_state,
                "sync_result": sync_result if isinstance(sync_result, dict) else {},
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if "permission denied" in message.lower():
            return _error("space_permission_denied", message)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("space_job_invalid", message)
    except Exception as e:
        if "permission denied" in str(e).lower():
            return _error("space_permission_denied", str(e))
        return _error("group_space_bind_failed", str(e))


def handle_group_space_ingest(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    provider_raw = args.get("provider")
    kind_raw = args.get("kind")
    payload_raw = args.get("payload")
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    try:
        group = _require_group(group_id)
        _assert_write_permission(group, by=by)
        provider = _provider_or_error(provider_raw)
        kind = _kind_or_error(kind_raw)
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        binding = get_space_binding(group.group_id, provider=provider)
        if not isinstance(binding, dict):
            return _error("space_binding_missing", "group is not bound to provider")
        if str(binding.get("status") or "") != "bound":
            return _error("space_binding_missing", "group space binding is not active")
        remote_space_id = str(binding.get("remote_space_id") or "").strip()
        if not remote_space_id:
            return _error("space_binding_missing", "binding has no remote_space_id")
        provider_state = get_space_provider_state(provider)
        if not bool(provider_state.get("enabled")) or str(provider_state.get("mode") or "") == "disabled":
            return _error("space_provider_disabled", "provider is disabled")
        job, deduped = enqueue_space_job(
            group_id=group.group_id,
            provider=provider,
            remote_space_id=remote_space_id,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        final_job = dict(job)
        if not deduped:
            final_job = execute_space_job(str(job.get("job_id") or ""))
        _sync_projection_best_effort(group.group_id, provider)
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "job_id": str(final_job.get("job_id") or ""),
                "accepted": True,
                "deduped": bool(deduped),
                "job": final_job,
                "queue_summary": space_queue_summary(group_id=group.group_id, provider=provider),
                "provider_mode": str(get_space_provider_state(provider).get("mode") or "disabled"),
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("space_job_invalid", message)
    except Exception as e:
        if "permission denied" in str(e).lower():
            return _error("space_permission_denied", str(e))
        return _error("group_space_ingest_failed", str(e))


def handle_group_space_query(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    provider_raw = args.get("provider")
    query_text = str(args.get("query") or "").strip()
    options = args.get("options") if isinstance(args.get("options"), dict) else {}
    if not query_text:
        return _error("space_job_invalid", "missing query")
    try:
        group = _require_group(group_id)
        provider = _provider_or_error(provider_raw)
        binding = get_space_binding(group.group_id, provider=provider)
        if not isinstance(binding, dict):
            return _error("space_binding_missing", "group is not bound to provider")
        remote_space_id = str(binding.get("remote_space_id") or "").strip()
        if not remote_space_id or str(binding.get("status") or "") != "bound":
            return _error("space_binding_missing", "group space binding is not active")
        provider_state = get_space_provider_state(provider)
        mode = str(provider_state.get("mode") or "disabled")
        enabled = bool(provider_state.get("enabled"))
        if (not enabled) or mode == "disabled":
            return DaemonResponse(
                ok=True,
                result={
                    "group_id": group.group_id,
                    "provider": provider,
                    "provider_mode": "disabled",
                    "degraded": True,
                    "answer": "",
                    "references": [],
                    "error": {"code": "space_provider_disabled", "message": "provider is disabled"},
                },
            )
        result = run_space_query(
            provider=provider,
            remote_space_id=remote_space_id,
            query=query_text,
            options=dict(options),
        )
        provider_after = get_space_provider_state(provider)
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider,
                "provider_mode": str(provider_after.get("mode") or mode),
                "degraded": bool(result.get("degraded")),
                "answer": str(result.get("answer") or ""),
                "references": list(result.get("references") or []),
                "error": result.get("error"),
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("space_job_invalid", message)
    except Exception as e:
        return _error("group_space_query_failed", str(e))


def handle_group_space_jobs(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    provider_raw = args.get("provider")
    action_raw = args.get("action")
    state_filter = str(args.get("state") or "").strip()
    try:
        group = _require_group(group_id)
        provider = _provider_or_error(provider_raw)
        action = _action_or_error(action_raw)
        if action == "list":
            if state_filter and state_filter not in _SPACE_JOB_STATES:
                return _error("space_job_invalid", f"invalid state: {state_filter}")
            limit = max(1, min(int(args.get("limit") or 50), 500))
            jobs = list_space_jobs(
                group_id=group.group_id,
                provider=provider,
                state=state_filter,
                limit=limit,
            )
            return DaemonResponse(
                ok=True,
                result={
                    "group_id": group.group_id,
                    "provider": provider,
                    "jobs": jobs,
                    "queue_summary": space_queue_summary(group_id=group.group_id, provider=provider),
                },
            )

        _assert_write_permission(group, by=by)
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return _error("space_job_invalid", "missing job_id")
        job = get_space_job(job_id)
        if not isinstance(job, dict):
            return _error("space_job_not_found", f"job not found: {job_id}")
        if str(job.get("group_id") or "") != group.group_id:
            return _error("space_job_not_found", f"job not found: {job_id}")
        if str(job.get("provider") or "") != provider:
            return _error("space_job_not_found", f"job not found: {job_id}")

        if action == "retry":
            updated = retry_space_job(job_id)
        elif action == "cancel":
            updated = cancel_space_job(job_id)
        else:
            return _error("space_job_invalid", f"invalid action: {action}")
        _sync_projection_best_effort(group.group_id, provider)

        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider,
                "job": updated,
                "queue_summary": space_queue_summary(group_id=group.group_id, provider=provider),
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if "permission denied" in message.lower():
            return _error("space_permission_denied", message)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        if message.startswith("cannot retry job in state=") or message.startswith("cannot cancel job in state="):
            return _error("space_job_state_conflict", message)
        return _error("space_job_invalid", message)
    except Exception as e:
        if "permission denied" in str(e).lower():
            return _error("space_permission_denied", str(e))
        if "cannot retry job in state=" in str(e):
            return _error("space_job_state_conflict", str(e))
        if "cannot cancel job in state=" in str(e):
            return _error("space_job_state_conflict", str(e))
        return _error("group_space_jobs_failed", str(e))


def handle_group_space_sync(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    provider_raw = args.get("provider")
    action_raw = args.get("action")
    force = bool(args.get("force") is True)
    try:
        group = _require_group(group_id)
        provider = _provider_or_error(provider_raw)
        action = _sync_action_or_error(action_raw)
        if action == "status":
            return DaemonResponse(
                ok=True,
                result={
                    "group_id": group.group_id,
                    "provider": provider,
                    "sync": read_group_space_sync_state(group.group_id),
                },
            )
        _assert_write_permission(group, by=by)
        result = sync_group_space_files(group.group_id, provider=provider, force=force)
        _sync_projection_best_effort(group.group_id, provider)
        if not bool(result.get("ok")):
            return _error(
                str(result.get("code") or "space_sync_failed"),
                str(result.get("message") or "group space sync failed"),
                details=result,
            )
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider,
                "sync": read_group_space_sync_state(group.group_id),
                "sync_result": result,
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if "permission denied" in message.lower():
            return _error("space_permission_denied", message)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("space_job_invalid", message)
    except Exception as e:
        if "permission denied" in str(e).lower():
            return _error("space_permission_denied", str(e))
        return _error("group_space_sync_failed", str(e))


def handle_group_space_provider_credential_status(args: Dict[str, Any]) -> DaemonResponse:
    provider_raw = args.get("provider")
    by = str(args.get("by") or "user").strip() or "user"
    if not _is_user_writer(by):
        return _error("space_permission_denied", "only user can read provider credentials")
    try:
        provider = _provider_or_error(provider_raw)
        status = _build_provider_credential_status(provider)
        return DaemonResponse(ok=True, result={"provider": provider, "credential": status})
    except ValueError as e:
        return _error("space_job_invalid", str(e))
    except Exception as e:
        return _error("group_space_provider_credential_status_failed", str(e))


def handle_group_space_provider_credential_update(args: Dict[str, Any]) -> DaemonResponse:
    provider_raw = args.get("provider")
    by = str(args.get("by") or "user").strip() or "user"
    clear = bool(args.get("clear") is True)
    auth_json = str(args.get("auth_json") or "").strip()
    if not _is_user_writer(by):
        return _error("space_permission_denied", "only user can update provider credentials")
    try:
        provider = _provider_or_error(provider_raw)
        secret_key = _provider_secret_key(provider)
        if clear:
            _ = update_space_provider_secrets(
                provider,
                set_vars={},
                unset_keys=[secret_key],
                clear=True,
            )
        else:
            if not auth_json:
                return _error("space_provider_not_configured", "missing auth_json")
            _ = parse_notebooklm_auth_json(auth_json, label=secret_key)
            _ = update_space_provider_secrets(
                provider,
                set_vars={secret_key: auth_json},
                unset_keys=[],
                clear=False,
            )
        status = _build_provider_credential_status(provider)
        return DaemonResponse(ok=True, result={"provider": provider, "credential": status})
    except NotebookLMProviderError as e:
        return _error(str(e.code or "space_provider_auth_invalid"), str(e))
    except ValueError as e:
        return _error("space_job_invalid", str(e))
    except Exception as e:
        return _error("group_space_provider_credential_update_failed", str(e))


def handle_group_space_provider_health_check(args: Dict[str, Any]) -> DaemonResponse:
    provider_raw = args.get("provider")
    by = str(args.get("by") or "user").strip() or "user"
    if not _is_user_writer(by):
        return _error("space_permission_denied", "only user can run provider health checks")
    try:
        provider = _provider_or_error(provider_raw)
        current_state = get_space_provider_state(provider)
        auth_json = _resolve_auth_json(provider)
        try:
            health = notebooklm_health_check(
                auth_json_raw=auth_json,
                real_enabled=bool(current_state.get("real_enabled")),
            )
            mode = "active" if bool(current_state.get("enabled")) else "disabled"
            provider_state = set_space_provider_state(
                provider,
                mode=mode,
                last_error="",
                touch_health=True,
            )
            return DaemonResponse(
                ok=True,
                result={
                    "provider": provider,
                    "healthy": True,
                    "health": dict(health or {}),
                    "provider_state": provider_state,
                    "credential": _build_provider_credential_status(provider),
                },
            )
        except NotebookLMProviderError as e:
            mode = "degraded" if bool(current_state.get("enabled")) else "disabled"
            provider_state = set_space_provider_state(
                provider,
                mode=mode,
                last_error=str(e),
                touch_health=True,
            )
            return DaemonResponse(
                ok=True,
                result={
                    "provider": provider,
                    "healthy": False,
                    "error": {"code": str(e.code or "space_provider_upstream_error"), "message": str(e)},
                    "provider_state": provider_state,
                    "credential": _build_provider_credential_status(provider),
                },
            )
    except ValueError as e:
        return _error("space_job_invalid", str(e))
    except Exception as e:
        return _error("group_space_provider_health_check_failed", str(e))


def handle_group_space_provider_auth(args: Dict[str, Any]) -> DaemonResponse:
    provider_raw = args.get("provider")
    by = str(args.get("by") or "user").strip() or "user"
    action_raw = args.get("action")
    timeout_seconds = int(args.get("timeout_seconds") or 900)
    if not _is_user_writer(by):
        return _error("space_permission_denied", "only user can run provider auth flow")
    try:
        provider = _provider_or_error(provider_raw)
        if provider != "notebooklm":
            return _error("space_job_invalid", f"unsupported provider auth flow: {provider}")
        action = _provider_auth_action_or_error(action_raw)
        if action == "start":
            auth = start_notebooklm_auth_flow(timeout_seconds=timeout_seconds)
        elif action == "cancel":
            auth = cancel_notebooklm_auth_flow()
        else:
            auth = get_notebooklm_auth_flow_status()
        provider_state = get_space_provider_state(provider)
        provider_state.update(_provider_runtime_readiness(provider))
        credential = _build_provider_credential_status(provider)
        return DaemonResponse(
            ok=True,
            result={
                "provider": provider,
                "provider_state": provider_state,
                "credential": credential,
                "auth": auth,
            },
        )
    except ValueError as e:
        return _error("space_job_invalid", str(e))
    except Exception as e:
        return _error("group_space_provider_auth_failed", str(e))


def try_handle_group_space_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    if op == "group_space_status":
        return handle_group_space_status(args)
    if op == "group_space_bind":
        return handle_group_space_bind(args)
    if op == "group_space_ingest":
        return handle_group_space_ingest(args)
    if op == "group_space_query":
        return handle_group_space_query(args)
    if op == "group_space_jobs":
        return handle_group_space_jobs(args)
    if op == "group_space_sync":
        return handle_group_space_sync(args)
    if op == "group_space_provider_credential_status":
        return handle_group_space_provider_credential_status(args)
    if op == "group_space_provider_credential_update":
        return handle_group_space_provider_credential_update(args)
    if op == "group_space_provider_health_check":
        return handle_group_space_provider_health_check(args)
    if op == "group_space_provider_auth":
        return handle_group_space_provider_auth(args)
    return None

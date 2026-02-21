"""Group Space (external memory control-plane) operation handlers for daemon."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse, SpaceBinding
from ...kernel.group import load_group
from ...kernel.permissions import require_group_permission
from ..group_space_runtime import execute_space_job, retry_space_job, run_space_query
from ..group_space_store import (
    cancel_space_job,
    enqueue_space_job,
    get_space_binding,
    get_space_job,
    get_space_provider_state,
    list_space_bindings,
    list_space_jobs,
    set_space_binding_unbound,
    set_space_provider_state,
    space_queue_summary,
    upsert_space_binding,
)

_SPACE_PROVIDER_IDS = {"notebooklm"}
_SPACE_JOB_KINDS = {"context_sync", "resource_ingest"}
_SPACE_JOB_STATES = {"pending", "running", "succeeded", "failed", "canceled"}
_SPACE_JOB_ACTIONS = {"list", "retry", "cancel"}


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


def handle_group_space_status(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    provider_raw = args.get("provider")
    try:
        group = _require_group(group_id)
        provider = _provider_or_error(provider_raw)
        provider_state = get_space_provider_state(provider)
        binding = get_space_binding(group.group_id, provider=provider) or _default_binding(group.group_id, provider)
        summary = space_queue_summary(group_id=group.group_id, provider=provider)
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider_state,
                "binding": binding,
                "queue_summary": summary,
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
        if action == "bind":
            if not remote_space_id:
                return _error("space_binding_conflict", "missing remote_space_id for bind")
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
                mode="active",
                last_error="",
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
        return DaemonResponse(
            ok=True,
            result={
                "group_id": group.group_id,
                "provider": provider_state,
                "binding": binding,
                "queue_summary": summary,
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
    return None

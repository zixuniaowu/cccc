from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from .group_space_provider import SpaceProviderError, provider_ingest, provider_query
from .group_space_store import (
    get_space_job,
    get_space_provider_state,
    list_due_space_jobs,
    mark_space_job_failed,
    mark_space_job_retry_scheduled,
    mark_space_job_running,
    mark_space_job_succeeded,
    reset_space_job_for_retry,
    set_space_provider_state,
)

_RETRY_BACKOFF_SECONDS = (2, 10)


def _utc_after_seconds(seconds: int) -> str:
    now = datetime.now(timezone.utc)
    return (now + timedelta(seconds=max(0, int(seconds)))).isoformat().replace("+00:00", "Z")


def _classify_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, SpaceProviderError):
        return {
            "code": exc.code or "space_upstream_error",
            "message": str(exc) or "provider error",
            "transient": bool(exc.transient),
            "degrade_provider": bool(exc.degrade_provider),
        }
    return {
        "code": "space_upstream_error",
        "message": str(exc) or "provider error",
        "transient": True,
        "degrade_provider": False,
    }


def execute_space_job(job_id: str) -> Dict[str, Any]:
    job = get_space_job(job_id)
    if not isinstance(job, dict):
        raise ValueError(f"job not found: {job_id}")
    state = str(job.get("state") or "")
    if state not in ("pending", "running"):
        return job

    current = mark_space_job_running(job_id)
    provider = str(current.get("provider") or "notebooklm").strip() or "notebooklm"
    remote_space_id = str(current.get("remote_space_id") or "").strip()
    kind = str(current.get("kind") or "context_sync").strip() or "context_sync"
    payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
    attempt = int(current.get("attempt") or 0)
    max_attempts = max(1, int(current.get("max_attempts") or 3))

    try:
        _ = provider_ingest(
            provider,
            remote_space_id=remote_space_id,
            kind=kind,
            payload=dict(payload),
        )
        set_space_provider_state(
            provider,
            enabled=True,
            mode="active",
            last_error="",
            touch_health=True,
        )
        return mark_space_job_succeeded(job_id)
    except Exception as exc:
        err = _classify_error(exc)
        if err["degrade_provider"]:
            set_space_provider_state(
                provider,
                enabled=True,
                mode="degraded",
                last_error=err["message"],
                touch_health=True,
            )
        if bool(err["transient"]) and attempt < max_attempts:
            idx = min(max(0, attempt - 1), len(_RETRY_BACKOFF_SECONDS) - 1)
            backoff = _RETRY_BACKOFF_SECONDS[idx]
            return mark_space_job_retry_scheduled(
                job_id,
                code=str(err["code"]),
                message=str(err["message"]),
                next_run_at=_utc_after_seconds(int(backoff)),
            )
        return mark_space_job_failed(job_id, code=str(err["code"]), message=str(err["message"]))


def retry_space_job(job_id: str) -> Dict[str, Any]:
    reset_space_job_for_retry(job_id)
    return execute_space_job(job_id)


def run_space_query(
    *,
    provider: str,
    remote_space_id: str,
    query: str,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        result = provider_query(
            provider,
            remote_space_id=remote_space_id,
            query=query,
            options=options,
        )
        set_space_provider_state(
            provider,
            enabled=True,
            mode="active",
            last_error="",
            touch_health=True,
        )
        return {
            "answer": str(result.get("answer") or ""),
            "references": list(result.get("references") or []),
            "degraded": False,
            "error": None,
        }
    except Exception as exc:
        err = _classify_error(exc)
        if err["degrade_provider"]:
            set_space_provider_state(
                provider,
                enabled=True,
                mode="degraded",
                last_error=err["message"],
                touch_health=True,
            )
        state = get_space_provider_state(provider)
        return {
            "answer": "",
            "references": [],
            "degraded": True,
            "error": {"code": str(err["code"]), "message": str(err["message"])},
            "provider_mode": str(state.get("mode") or "degraded"),
        }


def process_due_space_jobs(*, limit: int = 20) -> Dict[str, Any]:
    max_items = max(1, min(int(limit or 20), 200))
    due_jobs = list_due_space_jobs(limit=max_items)
    processed = 0
    succeeded = 0
    failed = 0
    rescheduled = 0
    for item in due_jobs:
        job_id = str(item.get("job_id") or "").strip()
        if not job_id:
            continue
        try:
            out = execute_space_job(job_id)
            processed += 1
            state = str(out.get("state") or "")
            if state == "succeeded":
                succeeded += 1
            elif state == "failed":
                failed += 1
            elif state == "pending":
                rescheduled += 1
        except Exception:
            failed += 1
    return {
        "seen": len(due_jobs),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "rescheduled": rescheduled,
    }

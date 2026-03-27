from __future__ import annotations

from typing import Any, Dict, List

from .context import ContextStorage
from .pet_signals import load_pet_signals
from .pet_task_proposals import build_task_proposal_candidates
from .pet_task_triage import build_task_triage_payload


def _load_tasks(group: Any) -> list[Any]:
    try:
        storage = ContextStorage(group)
        return list(storage.list_tasks() or [])
    except Exception:
        return []


def _reply_pressure_fallback(group: Any) -> Dict[str, Any]:
    group_id = str(getattr(group, "group_id", "") or "").strip()
    return {
        "id": "reply-pressure-oldest-followup",
        "kind": "task_proposal",
        "priority": 90,
        "summary": "先处理那条拖得最久的待回复线程",
        "agent": "pet-peer",
        "fingerprint": "task_proposal:reply_pressure:oldest_followup",
        "action": {
            "type": "task_proposal",
            "group_id": group_id,
            "operation": "update",
            "title": "处理最久未闭环的待回复线程",
            "text": "先处理拖得最久的待回复线程：给出当前结论，或明确还缺什么运行态证据，不要继续挂着。",
        },
        "source": {
            "suggestion_kind": "reply_pressure",
        },
        "updated_at": "",
    }


def _candidate_to_decision(group: Any, candidate: Dict[str, Any]) -> Dict[str, Any]:
    action = candidate.get("action") if isinstance(candidate.get("action"), dict) else {}
    reason = str(candidate.get("reason") or "").strip().lower() or "task"
    task_id = str(action.get("task_id") or "").strip() or "general"
    return {
        "id": f"pet-{reason}-{task_id}",
        "kind": "task_proposal",
        "priority": int(candidate.get("priority") or 0),
        "summary": str(candidate.get("summary") or "").strip(),
        "agent": "pet-peer",
        "fingerprint": f"task_proposal:{reason}:{task_id}",
        "action": {
            "type": "task_proposal",
            "group_id": str(getattr(group, "group_id", "") or "").strip(),
            "operation": str(action.get("operation") or "update").strip().lower() or "update",
            "task_id": task_id,
            "title": str(action.get("title") or "").strip(),
            "status": str(action.get("status") or "").strip(),
            "assignee": str(action.get("assignee") or "").strip(),
            "text": str(action.get("text") or "").strip(),
        },
        "source": {
            "task_id": task_id,
            "suggestion_kind": reason,
        },
        "updated_at": "",
    }


def build_fallback_pet_decisions(group: Any) -> List[Dict[str, Any]]:
    tasks = _load_tasks(group)
    task_triage = build_task_triage_payload(tasks, limit=3)
    signal_payload = load_pet_signals(group, context_payload=task_triage)
    proposal_ready = signal_payload.get("proposal_ready") if isinstance(signal_payload.get("proposal_ready"), dict) else {}
    if not bool(proposal_ready.get("ready")):
        return []

    focus = str(proposal_ready.get("focus") or "none").strip().lower()
    if focus == "reply_pressure":
        return [_reply_pressure_fallback(group)]

    candidates = build_task_proposal_candidates(tasks, signal_payload=signal_payload, limit=1)
    if not candidates:
        return []
    return [_candidate_to_decision(group, candidates[0])]

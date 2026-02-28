"""Capability registry and progressive MCP disclosure operation handlers."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import hashlib
import time
from importlib import resources as pkg_resources
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import yaml

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.actors import get_effective_role
from ...kernel.capabilities import (
    BUILTIN_CAPABILITY_PACKS,
    CORE_TOOL_NAMES,
    all_builtin_pack_ids,
    resolve_visible_tool_names,
)
from ...kernel.context import ContextStorage
from ...kernel.group import load_group
from ...paths import ensure_home
from ...util.fs import atomic_write_json, atomic_write_text, read_json
from ...util.time import parse_utc_iso, utc_now_iso

_SOURCE_IDS = (
    "mcp_registry_official",
    "anthropic_skills",
    "github_skills_curated",
    "skillsmp_remote",
    "clawhub_remote",
    "openclaw_skills_remote",
    "clawskills_remote",
)

_MCP_REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
_MCP_REGISTRY_PAGE_LIMIT = 100
_GITHUB_API_BASE = "https://api.github.com"
_RAW_GITHUB_BASE = "https://raw.githubusercontent.com"
_OPENCLAW_SKILLS_TREE_API = f"{_GITHUB_API_BASE}/repos/openclaw/skills/git/trees/main?recursive=1"
_OPENCLAW_SKILLS_BLOB_BASE = "https://raw.githubusercontent.com/openclaw/skills/main"
_CLAWSKILLS_DATA_URL_DEFAULT = "https://clawskills.co/skills-data.js"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ARG_TEMPLATE_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
_ENV_FORWARD_TEMPLATE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=\{[a-zA-Z_][a-zA-Z0-9_]*\}$")
_CLAWSKILLS_ENTRY_RE = re.compile(r"\{[^{}]*\}")
_STATE_LOCK = threading.RLock()
_CATALOG_LOCK = threading.RLock()
_RUNTIME_LOCK = threading.RLock()
_AUDIT_LOCK = threading.RLock()
_POLICY_LOCK = threading.RLock()
_REMOTE_SOURCE_CACHE_LOCK = threading.RLock()
_OPENCLAW_TREE_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "paths": []}

_LEVEL_INDEXED = "indexed"
_LEVEL_MOUNTED = "mounted"
_LEVEL_ENABLED = "enabled"
_LEVEL_PINNED = "pinned"
_LEVELS = {_LEVEL_INDEXED, _LEVEL_MOUNTED, _LEVEL_ENABLED, _LEVEL_PINNED}
_POLICY_LEVEL_ORDER = {
    _LEVEL_INDEXED: 0,
    _LEVEL_MOUNTED: 1,
    _LEVEL_ENABLED: 2,
    _LEVEL_PINNED: 3,
}
_POLICY_CACHE: Dict[str, Any] = {
    "key": "",
    "compiled": None,
    "source": "",
    "error": "",
}
_QUAL_QUALIFIED = "qualified"
_QUAL_BLOCKED = "blocked"
_QUAL_UNAVAILABLE = "unavailable"
_QUAL_STATES = {_QUAL_QUALIFIED, _QUAL_BLOCKED, _QUAL_UNAVAILABLE}


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(
        ok=False,
        error=DaemonError(code=code, message=message, details=(details or {})),
    )


def _capability_root() -> Path:
    return ensure_home() / "state" / "capabilities"


def _state_path() -> Path:
    return _capability_root() / "state.json"


def _catalog_path() -> Path:
    return _capability_root() / "catalog.json"


def _runtime_path() -> Path:
    return _capability_root() / "runtime.json"


def _audit_path() -> Path:
    return _capability_root() / "audit.jsonl"


def _source_state_template(sync_state: str = "never") -> Dict[str, Any]:
    return {
        "sync_state": sync_state,
        "last_synced_at": "",
        "staleness_seconds": None,
        "error": "",
        "record_count": 0,
        "next_cursor": "",
        "updated_since": "",
    }


def _new_state_doc() -> Dict[str, Any]:
    now = utc_now_iso()
    return {
        "v": 1,
        "created_at": now,
        "updated_at": now,
        "group_enabled": {},
        "actor_enabled": {},
        "session_enabled": {},
        "global_blocked": {},
        "group_blocked": {},
    }


def _new_catalog_doc() -> Dict[str, Any]:
    now = utc_now_iso()
    return {
        "v": 1,
        "created_at": now,
        "updated_at": now,
        "sources": {
            "mcp_registry_official": _source_state_template("never"),
            "anthropic_skills": _source_state_template("never"),
            "github_skills_curated": _source_state_template("never"),
            "skillsmp_remote": _source_state_template("never"),
            "clawhub_remote": _source_state_template("never"),
            "openclaw_skills_remote": _source_state_template("never"),
            "clawskills_remote": _source_state_template("never"),
        },
        "records": {},
    }


def _new_runtime_doc() -> Dict[str, Any]:
    now = utc_now_iso()
    return {
        "v": 2,
        "created_at": now,
        "updated_at": now,
        "artifacts": {},
        "capability_artifacts": {},
        "actor_instances": {},
        "recent_success": {},
    }


def _normalize_state_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return _new_state_doc()
    doc = dict(raw)
    now = utc_now_iso()
    doc["v"] = 1
    if not str(doc.get("created_at") or "").strip():
        doc["created_at"] = now
    if not str(doc.get("updated_at") or "").strip():
        doc["updated_at"] = now

    group_enabled_raw = doc.get("group_enabled")
    group_enabled: Dict[str, List[str]] = {}
    if isinstance(group_enabled_raw, dict):
        for group_id, items in group_enabled_raw.items():
            gid = str(group_id or "").strip()
            if not gid or not isinstance(items, list):
                continue
            clean = sorted({str(x or "").strip() for x in items if str(x or "").strip()})
            if clean:
                group_enabled[gid] = clean
    doc["group_enabled"] = group_enabled

    actor_enabled_raw = doc.get("actor_enabled")
    actor_enabled: Dict[str, Dict[str, List[str]]] = {}
    if isinstance(actor_enabled_raw, dict):
        for group_id, per_group in actor_enabled_raw.items():
            gid = str(group_id or "").strip()
            if not gid or not isinstance(per_group, dict):
                continue
            clean_group: Dict[str, List[str]] = {}
            for actor_id, items in per_group.items():
                aid = str(actor_id or "").strip()
                if not aid or not isinstance(items, list):
                    continue
                clean = sorted({str(x or "").strip() for x in items if str(x or "").strip()})
                if clean:
                    clean_group[aid] = clean
            if clean_group:
                actor_enabled[gid] = clean_group
    doc["actor_enabled"] = actor_enabled

    session_enabled_raw = doc.get("session_enabled")
    session_enabled: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    if isinstance(session_enabled_raw, dict):
        for group_id, per_group in session_enabled_raw.items():
            gid = str(group_id or "").strip()
            if not gid or not isinstance(per_group, dict):
                continue
            clean_group: Dict[str, List[Dict[str, str]]] = {}
            for actor_id, entries in per_group.items():
                aid = str(actor_id or "").strip()
                if not aid or not isinstance(entries, list):
                    continue
                clean_entries: List[Dict[str, str]] = []
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    cap_id = str(item.get("capability_id") or "").strip()
                    expires_at = str(item.get("expires_at") or "").strip()
                    if not cap_id or not expires_at:
                        continue
                    if parse_utc_iso(expires_at) is None:
                        continue
                    clean_entries.append({"capability_id": cap_id, "expires_at": expires_at})
                if clean_entries:
                    clean_group[aid] = clean_entries
            if clean_group:
                session_enabled[gid] = clean_group
    doc["session_enabled"] = session_enabled

    def _normalize_block_entry(item: Any) -> Optional[Dict[str, str]]:
        if not isinstance(item, dict):
            return None
        blocked_at = str(item.get("blocked_at") or "").strip()
        if parse_utc_iso(blocked_at) is None:
            blocked_at = now
        expires_at = str(item.get("expires_at") or "").strip()
        if expires_at and parse_utc_iso(expires_at) is None:
            expires_at = ""
        return {
            "reason": str(item.get("reason") or "").strip(),
            "by": str(item.get("by") or "").strip(),
            "blocked_at": blocked_at,
            "expires_at": expires_at,
        }

    global_blocked_raw = doc.get("global_blocked")
    global_blocked: Dict[str, Dict[str, str]] = {}
    if isinstance(global_blocked_raw, dict):
        for capability_id, entry in global_blocked_raw.items():
            cap_id = str(capability_id or "").strip()
            if not cap_id:
                continue
            normalized = _normalize_block_entry(entry)
            if normalized is None:
                continue
            global_blocked[cap_id] = normalized
    doc["global_blocked"] = global_blocked

    group_blocked_raw = doc.get("group_blocked")
    group_blocked: Dict[str, Dict[str, Dict[str, str]]] = {}
    if isinstance(group_blocked_raw, dict):
        for group_id, per_group in group_blocked_raw.items():
            gid = str(group_id or "").strip()
            if not gid or not isinstance(per_group, dict):
                continue
            clean_group: Dict[str, Dict[str, str]] = {}
            for capability_id, entry in per_group.items():
                cap_id = str(capability_id or "").strip()
                if not cap_id:
                    continue
                normalized = _normalize_block_entry(entry)
                if normalized is None:
                    continue
                clean_group[cap_id] = normalized
            if clean_group:
                group_blocked[gid] = clean_group
    doc["group_blocked"] = group_blocked
    return doc


def _normalize_catalog_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return _new_catalog_doc()
    doc = dict(raw)
    now = utc_now_iso()
    doc["v"] = 1
    if not str(doc.get("created_at") or "").strip():
        doc["created_at"] = now
    if not str(doc.get("updated_at") or "").strip():
        doc["updated_at"] = now

    sources_raw = doc.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    normalized_sources: Dict[str, Dict[str, Any]] = {}
    for source_id in _SOURCE_IDS:
        item = sources.get(source_id)
        normalized = _source_state_template("never")
        if isinstance(item, dict):
            normalized.update(
                {
                    "sync_state": str(item.get("sync_state") or "never"),
                    "last_synced_at": str(item.get("last_synced_at") or ""),
                    "staleness_seconds": item.get("staleness_seconds"),
                    "error": str(item.get("error") or ""),
                    "record_count": int(item.get("record_count") or 0),
                    "next_cursor": str(item.get("next_cursor") or ""),
                    "updated_since": str(item.get("updated_since") or ""),
                }
            )
        normalized_sources[source_id] = normalized
    doc["sources"] = normalized_sources

    records_raw = doc.get("records")
    records: Dict[str, Dict[str, Any]] = {}
    if isinstance(records_raw, dict):
        for capability_id, item in records_raw.items():
            cap_id = str(capability_id or "").strip()
            if not cap_id or not isinstance(item, dict):
                continue
            candidate = dict(item)
            candidate["capability_id"] = cap_id
            records[cap_id] = candidate
    doc["records"] = records
    return doc


def _normalize_runtime_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return _new_runtime_doc()
    doc = dict(raw)
    now = utc_now_iso()
    doc["v"] = 2
    if not str(doc.get("created_at") or "").strip():
        doc["created_at"] = now
    if not str(doc.get("updated_at") or "").strip():
        doc["updated_at"] = now
    artifacts_raw = doc.get("artifacts")
    artifacts: Dict[str, Dict[str, Any]] = {}
    if isinstance(artifacts_raw, dict):
        for artifact_id, item in artifacts_raw.items():
            aid = str(artifact_id or "").strip()
            if not aid or not isinstance(item, dict):
                continue
            tools_raw = item.get("tools")
            tools: List[Dict[str, Any]] = []
            if isinstance(tools_raw, list):
                for tool in tools_raw:
                    if not isinstance(tool, dict):
                        continue
                    name = str(tool.get("name") or "").strip()
                    real_name = str(tool.get("real_tool_name") or "").strip()
                    if not name or not real_name:
                        continue
                    schema = tool.get("inputSchema")
                    if not isinstance(schema, dict):
                        schema = {"type": "object", "properties": {}, "required": []}
                    tools.append(
                        {
                            "name": name,
                            "real_tool_name": real_name,
                            "description": str(tool.get("description") or "").strip(),
                            "inputSchema": schema,
                        }
                    )
            cap_ids_raw = item.get("capability_ids")
            cap_ids: List[str] = []
            if isinstance(cap_ids_raw, list):
                seen: set[str] = set()
                for x in cap_ids_raw:
                    cid = str(x or "").strip()
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    cap_ids.append(cid)
            artifacts[aid] = {
                "artifact_id": aid,
                "install_key": str(item.get("install_key") or "").strip() or aid,
                "state": str(item.get("state") or "").strip() or "unknown",
                "installer": str(item.get("installer") or "").strip(),
                "install_mode": str(item.get("install_mode") or "").strip(),
                "invoker": dict(item.get("invoker")) if isinstance(item.get("invoker"), dict) else {},
                "tools": tools,
                "last_error": str(item.get("last_error") or "").strip(),
                "last_error_code": str(item.get("last_error_code") or "").strip(),
                "updated_at": str(item.get("updated_at") or "").strip() or now,
                "capability_ids": cap_ids,
            }

    capability_artifacts_raw = doc.get("capability_artifacts")
    capability_artifacts: Dict[str, str] = {}
    if isinstance(capability_artifacts_raw, dict):
        for capability_id, artifact_id in capability_artifacts_raw.items():
            cid = str(capability_id or "").strip()
            aid = str(artifact_id or "").strip()
            if not cid or not aid:
                continue
            if aid in artifacts:
                capability_artifacts[cid] = aid

    # Legacy migration path: installations -> artifacts/capability_artifacts.
    installs_raw = doc.get("installations")
    if isinstance(installs_raw, dict):
        for capability_id, item in installs_raw.items():
            cid = str(capability_id or "").strip()
            if not cid or not isinstance(item, dict):
                continue
            aid = str(capability_artifacts.get(cid) or "").strip()
            if not aid:
                aid = f"art_{hashlib.sha1(f'legacy:{cid}'.encode('utf-8')).hexdigest()[:16]}"
            existing = artifacts.get(aid) if isinstance(artifacts.get(aid), dict) else None
            tools_raw = item.get("tools")
            tools: List[Dict[str, Any]] = []
            if isinstance(tools_raw, list):
                for tool in tools_raw:
                    if not isinstance(tool, dict):
                        continue
                    name = str(tool.get("name") or "").strip()
                    real_name = str(tool.get("real_tool_name") or "").strip()
                    if not name or not real_name:
                        continue
                    schema = tool.get("inputSchema")
                    if not isinstance(schema, dict):
                        schema = {"type": "object", "properties": {}, "required": []}
                    tools.append(
                        {
                            "name": name,
                            "real_tool_name": real_name,
                            "description": str(tool.get("description") or "").strip(),
                            "inputSchema": schema,
                        }
                    )
            merged_caps = (
                list(existing.get("capability_ids") or []) if isinstance(existing, dict) else []
            )
            if cid not in merged_caps:
                merged_caps.append(cid)
            artifacts[aid] = {
                "artifact_id": aid,
                "install_key": str((existing or {}).get("install_key") or aid).strip() or aid,
                "state": str(item.get("state") or (existing or {}).get("state") or "").strip() or "unknown",
                "installer": str(item.get("installer") or (existing or {}).get("installer") or "").strip(),
                "install_mode": str(item.get("install_mode") or (existing or {}).get("install_mode") or "").strip(),
                "invoker": dict(item.get("invoker")) if isinstance(item.get("invoker"), dict) else (
                    dict((existing or {}).get("invoker")) if isinstance((existing or {}).get("invoker"), dict) else {}
                ),
                "tools": tools or (list((existing or {}).get("tools") or [])),
                "last_error": str(item.get("last_error") or (existing or {}).get("last_error") or "").strip(),
                "last_error_code": str(
                    item.get("last_error_code") or (existing or {}).get("last_error_code") or ""
                ).strip(),
                "updated_at": str(item.get("updated_at") or (existing or {}).get("updated_at") or "").strip() or now,
                "capability_ids": merged_caps,
            }
            capability_artifacts[cid] = aid

    # Ensure reverse references are consistent.
    for cid, aid in list(capability_artifacts.items()):
        art = artifacts.get(aid)
        if not isinstance(art, dict):
            capability_artifacts.pop(cid, None)
            continue
        cap_ids = art.get("capability_ids") if isinstance(art.get("capability_ids"), list) else []
        if cid not in cap_ids:
            cap_ids.append(cid)
            art["capability_ids"] = cap_ids
            artifacts[aid] = art

    actor_instances_raw = doc.get("actor_instances")
    if not isinstance(actor_instances_raw, dict):
        actor_instances_raw = doc.get("actor_bindings")
    actor_instances: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    if isinstance(actor_instances_raw, dict):
        for group_id, per_group in actor_instances_raw.items():
            gid = str(group_id or "").strip()
            if not gid or not isinstance(per_group, dict):
                continue
            clean_group: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for actor_id, per_actor in per_group.items():
                aid = str(actor_id or "").strip()
                if not aid or not isinstance(per_actor, dict):
                    continue
                clean_actor: Dict[str, Dict[str, Any]] = {}
                for capability_id, item in per_actor.items():
                    cid = str(capability_id or "").strip()
                    if not cid or not isinstance(item, dict):
                        continue
                    artifact_id = str(item.get("artifact_id") or capability_artifacts.get(cid) or "").strip()
                    if artifact_id and artifact_id not in artifacts:
                        artifact_id = ""
                    clean_actor[cid] = {
                        "artifact_id": artifact_id,
                        "state": str(item.get("state") or "").strip() or "unknown",
                        "last_error": str(item.get("last_error") or "").strip(),
                        "updated_at": str(item.get("updated_at") or "").strip() or now,
                    }
                if clean_actor:
                    clean_group[aid] = clean_actor
            if clean_group:
                actor_instances[gid] = clean_group

    recent_success_raw = doc.get("recent_success")
    recent_success: Dict[str, Dict[str, Any]] = {}
    if isinstance(recent_success_raw, dict):
        for capability_id, row in recent_success_raw.items():
            cid = str(capability_id or "").strip()
            if not cid or not isinstance(row, dict):
                continue
            count = int(row.get("success_count") or 0)
            if count <= 0:
                continue
            recent_success[cid] = {
                "capability_id": cid,
                "success_count": count,
                "last_success_at": str(row.get("last_success_at") or "").strip() or now,
                "last_group_id": str(row.get("last_group_id") or "").strip(),
                "last_actor_id": str(row.get("last_actor_id") or "").strip(),
                "last_action": str(row.get("last_action") or "").strip(),
            }

    doc["artifacts"] = artifacts
    doc["capability_artifacts"] = capability_artifacts
    doc["actor_instances"] = actor_instances
    doc["recent_success"] = recent_success
    return doc


def _load_state_doc() -> Tuple[Path, Dict[str, Any]]:
    path = _state_path()
    return path, _normalize_state_doc(read_json(path))


def _save_state_doc(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _load_catalog_doc() -> Tuple[Path, Dict[str, Any]]:
    path = _catalog_path()
    return path, _normalize_catalog_doc(read_json(path))


def _save_catalog_doc(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _load_runtime_doc() -> Tuple[Path, Dict[str, Any]]:
    path = _runtime_path()
    return path, _normalize_runtime_doc(read_json(path))


def _save_runtime_doc(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _runtime_artifacts(runtime_doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = runtime_doc.get("artifacts")
    return raw if isinstance(raw, dict) else {}


def _runtime_capability_artifacts(runtime_doc: Dict[str, Any]) -> Dict[str, str]:
    raw = runtime_doc.get("capability_artifacts")
    return raw if isinstance(raw, dict) else {}


def _runtime_actor_bindings(runtime_doc: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    raw = runtime_doc.get("actor_instances")
    return raw if isinstance(raw, dict) else {}


def _runtime_recent_success(runtime_doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = runtime_doc.get("recent_success")
    return raw if isinstance(raw, dict) else {}


def _record_runtime_recent_success(
    runtime_doc: Dict[str, Any],
    *,
    capability_id: str,
    group_id: str = "",
    actor_id: str = "",
    action: str = "",
) -> None:
    cap_id = str(capability_id or "").strip()
    if not cap_id:
        return
    now_iso = utc_now_iso()
    mapping = _runtime_recent_success(runtime_doc)
    prior = mapping.get(cap_id) if isinstance(mapping.get(cap_id), dict) else {}
    count = max(0, int(prior.get("success_count") or 0)) + 1
    row: Dict[str, Any] = {
        "capability_id": cap_id,
        "success_count": count,
        "last_success_at": now_iso,
        "last_group_id": str(group_id or prior.get("last_group_id") or "").strip(),
        "last_actor_id": str(actor_id or prior.get("last_actor_id") or "").strip(),
        "last_action": str(action or prior.get("last_action") or "").strip(),
    }
    mapping[cap_id] = row
    keep_limit = _quota_limit("CCCC_CAPABILITY_RECENT_SUCCESS_LIMIT", 512, minimum=50, maximum=5000)
    if len(mapping) > keep_limit:
        ranked = sorted(
            (
                (
                    str(v.get("last_success_at") or ""),
                    str(k),
                )
                for k, v in mapping.items()
                if isinstance(v, dict)
            )
        )
        overflow = max(0, len(mapping) - keep_limit)
        for _, key in ranked[:overflow]:
            mapping.pop(str(key), None)
    runtime_doc["recent_success"] = mapping


def _set_runtime_capability_artifact(
    runtime_doc: Dict[str, Any],
    *,
    capability_id: str,
    artifact_id: str,
) -> None:
    cid = str(capability_id or "").strip()
    aid = str(artifact_id or "").strip()
    if not cid:
        return
    mapping = _runtime_capability_artifacts(runtime_doc)
    if aid:
        mapping[cid] = aid
    else:
        mapping.pop(cid, None)
    runtime_doc["capability_artifacts"] = mapping


def _runtime_install_for_capability(
    runtime_doc: Dict[str, Any],
    *,
    capability_id: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    cid = str(capability_id or "").strip()
    if not cid:
        return "", None
    mapping = _runtime_capability_artifacts(runtime_doc)
    artifacts = _runtime_artifacts(runtime_doc)
    aid = str(mapping.get(cid) or "").strip()
    if not aid:
        return "", None
    art = artifacts.get(aid) if isinstance(artifacts.get(aid), dict) else None
    return aid, (dict(art) if isinstance(art, dict) else None)


def _remove_runtime_capability_artifact(
    runtime_doc: Dict[str, Any],
    *,
    capability_id: str,
) -> str:
    cid = str(capability_id or "").strip()
    if not cid:
        return ""
    mapping = _runtime_capability_artifacts(runtime_doc)
    aid = str(mapping.pop(cid, "") or "").strip()
    runtime_doc["capability_artifacts"] = mapping
    if aid:
        artifacts = _runtime_artifacts(runtime_doc)
        row = artifacts.get(aid) if isinstance(artifacts.get(aid), dict) else None
        if isinstance(row, dict):
            caps_raw = row.get("capability_ids")
            caps = [str(x).strip() for x in caps_raw if str(x).strip()] if isinstance(caps_raw, list) else []
            caps = [x for x in caps if x != cid]
            row["capability_ids"] = caps
            artifacts[aid] = row
            runtime_doc["artifacts"] = artifacts
    return aid


def _remove_runtime_artifact_if_unreferenced(
    runtime_doc: Dict[str, Any],
    *,
    artifact_id: str,
) -> bool:
    aid = str(artifact_id or "").strip()
    if not aid:
        return False
    mapping = _runtime_capability_artifacts(runtime_doc)
    for mapped in mapping.values():
        if str(mapped or "").strip() == aid:
            return False
    artifacts = _runtime_artifacts(runtime_doc)
    if aid in artifacts:
        artifacts.pop(aid, None)
        runtime_doc["artifacts"] = artifacts
        return True
    return False


def _binding_state_allows_external_tool(state: Any) -> bool:
    token = str(state or "").strip().lower()
    if not token:
        return False
    return token in {"ready", "ready_cached", "tool_call_failed"}


def _install_state_allows_external_tool(state: Any) -> bool:
    token = str(state or "").strip().lower()
    if not token:
        return False
    return token in {"installed", "installed_degraded"}


def _set_runtime_actor_binding(
    runtime_doc: Dict[str, Any],
    *,
    group_id: str,
    actor_id: str,
    capability_id: str,
    artifact_id: str = "",
    state: str,
    last_error: str = "",
) -> None:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    cid = str(capability_id or "").strip()
    if not gid or not aid or not cid:
        return
    bindings = _runtime_actor_bindings(runtime_doc)
    per_group = bindings.setdefault(gid, {})
    per_actor = per_group.setdefault(aid, {})
    per_actor[cid] = {
        "artifact_id": str(artifact_id or "").strip(),
        "state": str(state or "").strip() or "unknown",
        "last_error": str(last_error or "").strip(),
        "updated_at": utc_now_iso(),
    }
    runtime_doc["actor_instances"] = bindings


def _remove_runtime_actor_binding(
    runtime_doc: Dict[str, Any],
    *,
    group_id: str,
    actor_id: str,
    capability_id: str,
) -> int:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    cid = str(capability_id or "").strip()
    if not gid or not aid or not cid:
        return 0
    bindings = _runtime_actor_bindings(runtime_doc)
    per_group = bindings.get(gid) if isinstance(bindings.get(gid), dict) else {}
    per_actor = per_group.get(aid) if isinstance(per_group.get(aid), dict) else {}
    if cid not in per_actor:
        return 0
    per_actor.pop(cid, None)
    if per_actor:
        per_group[aid] = per_actor
    else:
        per_group.pop(aid, None)
    if per_group:
        bindings[gid] = per_group
    else:
        bindings.pop(gid, None)
    runtime_doc["actor_instances"] = bindings
    return 1


def _remove_runtime_group_capability_bindings(
    runtime_doc: Dict[str, Any],
    *,
    group_id: str,
    capability_id: str,
) -> int:
    gid = str(group_id or "").strip()
    cid = str(capability_id or "").strip()
    if not gid or not cid:
        return 0
    bindings = _runtime_actor_bindings(runtime_doc)
    per_group = bindings.get(gid) if isinstance(bindings.get(gid), dict) else {}
    removed = 0
    actor_ids = list(per_group.keys()) if isinstance(per_group, dict) else []
    for aid in actor_ids:
        per_actor = per_group.get(aid) if isinstance(per_group.get(aid), dict) else {}
        if cid in per_actor:
            per_actor.pop(cid, None)
            removed += 1
        if per_actor:
            per_group[aid] = per_actor
        else:
            per_group.pop(aid, None)
    if per_group:
        bindings[gid] = per_group
    else:
        bindings.pop(gid, None)
    runtime_doc["actor_instances"] = bindings
    return removed


def _remove_runtime_capability_bindings_all_groups(
    runtime_doc: Dict[str, Any],
    *,
    capability_id: str,
) -> int:
    cid = str(capability_id or "").strip()
    if not cid:
        return 0
    bindings = _runtime_actor_bindings(runtime_doc)
    removed = 0
    group_ids = list(bindings.keys()) if isinstance(bindings, dict) else []
    for gid in group_ids:
        per_group = bindings.get(gid) if isinstance(bindings.get(gid), dict) else {}
        actor_ids = list(per_group.keys()) if isinstance(per_group, dict) else []
        for aid in actor_ids:
            per_actor = per_group.get(aid) if isinstance(per_group.get(aid), dict) else {}
            if cid in per_actor:
                per_actor.pop(cid, None)
                removed += 1
            if per_actor:
                per_group[aid] = per_actor
            else:
                per_group.pop(aid, None)
        if per_group:
            bindings[gid] = per_group
        else:
            bindings.pop(gid, None)
    runtime_doc["actor_instances"] = bindings
    return removed


def _append_audit_event(
    *,
    action_id: str,
    op: str,
    group_id: str,
    actor_id: str,
    by: str,
    capability_id: str,
    scope: str,
    enabled: bool,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    event = {
        "v": 1,
        "event_id": f"cae_{uuid.uuid4().hex}",
        "at": utc_now_iso(),
        "action_id": str(action_id or "").strip(),
        "op": str(op or "").strip(),
        "group_id": str(group_id or "").strip(),
        "actor_id": str(actor_id or "").strip(),
        "by": str(by or "").strip(),
        "capability_id": str(capability_id or "").strip(),
        "scope": str(scope or "").strip(),
        "enabled": bool(enabled),
        "outcome": str(outcome or "").strip(),
        "details": details if isinstance(details, dict) else {},
    }
    line = json.dumps(event, ensure_ascii=False, default=str)
    with _AUDIT_LOCK:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _ensure_group(group_id: str):
    gid = str(group_id or "").strip()
    if not gid:
        raise ValueError("missing_group_id")
    group = load_group(gid)
    if group is None:
        raise LookupError(f"group not found: {gid}")
    return group


def _is_foreman(group: Any, actor_id: str) -> bool:
    aid = str(actor_id or "").strip()
    if not aid:
        return False
    try:
        return str(get_effective_role(group, aid) or "") == "foreman"
    except Exception:
        return False


def _normalize_scope(raw_scope: Any) -> str:
    scope = str(raw_scope or "session").strip().lower()
    if scope not in {"group", "actor", "session"}:
        raise ValueError(f"invalid scope: {scope}")
    return scope


def _http_get_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> Any:
    req = Request(url, method="GET")
    all_headers = {"Accept": "application/json"}
    if isinstance(headers, dict):
        all_headers.update({str(k): str(v) for k, v in headers.items()})
    for k, v in all_headers.items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _http_get_json_obj(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> Dict[str, Any]:
    data = _http_get_json(url, headers=headers, timeout=timeout)
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return data


def _http_get_text(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> str:
    req = Request(url, method="GET")
    all_headers = {"Accept": "text/plain, text/markdown, text/html;q=0.9, */*;q=0.8"}
    if isinstance(headers, dict):
        all_headers.update({str(k): str(v) for k, v in headers.items()})
    for k, v in all_headers.items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _quota_limit(name: str, default: int, *, minimum: int = 1, maximum: int = 10_000) -> int:
    raw = _env_int(name, default)
    return max(minimum, min(int(raw or default), maximum))


def _normalize_policy_level(raw: Any, *, default: str = _LEVEL_INDEXED) -> str:
    level = str(raw or "").strip().lower()
    if level not in _LEVELS:
        return default
    return level


def _policy_level_visible(level: str) -> bool:
    return _normalize_policy_level(level) != _LEVEL_INDEXED


def _policy_default_compiled() -> Dict[str, Any]:
    return {
        "source_levels": {
            "cccc_builtin": _LEVEL_ENABLED,
            "anthropic_skills": _LEVEL_MOUNTED,
            "github_skills_curated": _LEVEL_MOUNTED,
            "skillsmp_remote": _LEVEL_MOUNTED,
            "clawhub_remote": _LEVEL_MOUNTED,
            "openclaw_skills_remote": _LEVEL_MOUNTED,
            "clawskills_remote": _LEVEL_MOUNTED,
            "mcp_registry_official": _LEVEL_MOUNTED,
        },
        "capability_levels": {},
        "skill_source_levels": {},
        "role_pinned": {},
        "curated_mcp_entries": [],
        "curated_skill_entries": [],
    }


def _allowlist_default_source_label() -> str:
    return "builtin:cccc.resources/capability-allowlist.default.yaml"


def _allowlist_user_overlay_path() -> Path:
    return ensure_home() / "config" / "capability-allowlist.user.yaml"


def _safe_load_yaml_mapping(text: str) -> Dict[str, Any]:
    raw = yaml.safe_load(str(text or ""))
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    raise ValueError("allowlist YAML root must be a mapping")


def _load_allowlist_default_doc() -> Tuple[Dict[str, Any], str]:
    try:
        text = pkg_resources.files("cccc.resources").joinpath("capability-allowlist.default.yaml").read_text(
            encoding="utf-8"
        )
    except Exception:
        text = ""
    try:
        doc = _safe_load_yaml_mapping(text)
    except Exception:
        doc = {}
    return doc, text


def _load_allowlist_overlay_doc() -> Tuple[Dict[str, Any], str, str]:
    path = _allowlist_user_overlay_path()
    if not path.exists() or not path.is_file():
        return {}, "", ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}, "", "failed_to_read_overlay"
    try:
        return _safe_load_yaml_mapping(text), text, ""
    except Exception as e:
        return {}, text, f"invalid_overlay_yaml:{e}"


def _merge_allowlist_docs(base: Any, overlay: Any) -> Dict[str, Any]:
    def _merge(a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            out: Dict[str, Any] = {str(k): v for k, v in a.items()}
            for key, value in b.items():
                sk = str(key)
                if sk in out:
                    out[sk] = _merge(out.get(sk), value)
                else:
                    out[sk] = value
            return out
        # Non-mapping types (including lists) are replaced by overlay value.
        return b

    base_doc = dict(base) if isinstance(base, dict) else {}
    overlay_doc = dict(overlay) if isinstance(overlay, dict) else {}
    merged = _merge(base_doc, overlay_doc)
    return merged if isinstance(merged, dict) else {}


def _allowlist_effective_snapshot() -> Dict[str, Any]:
    default_doc, default_text = _load_allowlist_default_doc()
    overlay_doc, overlay_text, overlay_error = _load_allowlist_overlay_doc()
    effective_doc = _merge_allowlist_docs(default_doc, overlay_doc)
    key_payload = json.dumps(
        {"default": default_doc, "overlay": overlay_doc},
        ensure_ascii=False,
        sort_keys=True,
    )
    revision = hashlib.sha1(key_payload.encode("utf-8")).hexdigest()
    return {
        "revision": revision,
        "default": default_doc,
        "overlay": overlay_doc,
        "effective": effective_doc,
        "default_source": _allowlist_default_source_label(),
        "overlay_source": str(_allowlist_user_overlay_path()) if overlay_text else "",
        "overlay_error": overlay_error,
        "default_text": default_text,
        "overlay_text": overlay_text,
    }


def _write_allowlist_overlay_doc(overlay_doc: Dict[str, Any]) -> None:
    path = _allowlist_user_overlay_path()
    root = path.parent
    root.mkdir(parents=True, exist_ok=True)
    doc = overlay_doc if isinstance(overlay_doc, dict) else {}
    if not doc:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return
    text = yaml.safe_dump(
        doc,
        allow_unicode=False,
        sort_keys=True,
    )
    atomic_write_text(path, text, encoding="utf-8")


def _clear_policy_cache() -> None:
    _POLICY_CACHE["key"] = ""
    _POLICY_CACHE["compiled"] = None
    _POLICY_CACHE["source"] = ""
    _POLICY_CACHE["error"] = ""


def _compile_allowlist_policy(raw: Any) -> Dict[str, Any]:
    compiled = _policy_default_compiled()
    doc = raw if isinstance(raw, dict) else {}

    defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
    source_levels = defaults.get("source_level") if isinstance(defaults.get("source_level"), dict) else {}
    for source_id, level in source_levels.items():
        sid = str(source_id or "").strip()
        if not sid:
            continue
        compiled["source_levels"][sid] = _normalize_policy_level(level, default=_LEVEL_INDEXED)

    capability_levels: Dict[str, str] = {}
    curated_mcp_entries: List[Dict[str, Any]] = []
    for item in doc.get("mcp_overrides") if isinstance(doc.get("mcp_overrides"), list) else []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("capability_id") or "").strip()
        if not cid:
            continue
        level = _normalize_policy_level(item.get("level"), default=_LEVEL_MOUNTED)
        capability_levels[cid] = level
        curated_mcp_entries.append(
            {
                "capability_id": cid,
                "level": level,
                "trust": str(item.get("trust") or "").strip().lower(),
                "notes": str(item.get("notes") or "").strip(),
                "install_mode_preference": str(item.get("install_mode_preference") or "").strip(),
                "risk_tags": list(item.get("risk_tags") or []) if isinstance(item.get("risk_tags"), list) else [],
                "required_secrets": (
                    list(item.get("required_secrets") or []) if isinstance(item.get("required_secrets"), list) else []
                ),
            }
        )

    skills = doc.get("skills") if isinstance(doc.get("skills"), dict) else {}
    skill_source_levels: Dict[str, str] = {}
    for item in skills.get("source_overrides") if isinstance(skills.get("source_overrides"), list) else []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("source_id") or "").strip()
        if not sid:
            continue
        skill_source_levels[sid] = _normalize_policy_level(item.get("level"), default=_LEVEL_MOUNTED)

    role_pinned: Dict[str, set[str]] = {}
    curated_skill_entries: List[Dict[str, Any]] = []

    def _append_skill_entry(raw_item: Dict[str, Any], *, default_source_id: str) -> None:
        cid = str(raw_item.get("capability_id") or "").strip()
        if not cid:
            return
        level = _normalize_policy_level(raw_item.get("level"), default=_LEVEL_MOUNTED)
        capability_levels[cid] = level
        source_id = str(raw_item.get("source_id") or default_source_id).strip() or default_source_id
        trust = str(raw_item.get("trust") or "").strip().lower()
        notes = str(raw_item.get("notes") or "").strip()
        name = str(raw_item.get("name") or "").strip()
        source_uri = str(raw_item.get("source_uri") or "").strip()
        description_short = str(raw_item.get("description_short") or "").strip()
        capsule_text = str(raw_item.get("capsule_text") or "").strip()
        license_text = str(raw_item.get("license") or "").strip()
        qualification_status = str(raw_item.get("qualification_status") or "").strip().lower()
        if qualification_status not in _QUAL_STATES:
            qualification_status = ""
        tags = list(raw_item.get("tags") or []) if isinstance(raw_item.get("tags"), list) else []
        requires_caps = (
            list(raw_item.get("requires_capabilities") or [])
            if isinstance(raw_item.get("requires_capabilities"), list)
            else []
        )
        reasons = (
            [str(x).strip() for x in raw_item.get("qualification_reasons") if str(x).strip()]
            if isinstance(raw_item.get("qualification_reasons"), list)
            else []
        )
        curated_skill_entries.append(
            {
                "capability_id": cid,
                "level": level,
                "source_id": source_id,
                "trust": trust,
                "notes": notes,
                "name": name,
                "source_uri": source_uri,
                "description_short": description_short,
                "capsule_text": capsule_text,
                "license": license_text,
                "qualification_status": qualification_status,
                "qualification_reasons": reasons,
                "tags": tags,
                "requires_capabilities": requires_caps,
            }
        )
        for role_raw in raw_item.get("pinned_roles") if isinstance(raw_item.get("pinned_roles"), list) else []:
            role = str(role_raw or "").strip().lower()
            if not role:
                continue
            role_pinned.setdefault(role, set()).add(cid)

    for item in skills.get("official_anthropic") if isinstance(skills.get("official_anthropic"), list) else []:
        if isinstance(item, dict):
            _append_skill_entry(item, default_source_id="anthropic_skills")

    for item in skills.get("curated") if isinstance(skills.get("curated"), list) else []:
        if isinstance(item, dict):
            _append_skill_entry(item, default_source_id="github_skills_curated")

    role_defaults = doc.get("role_defaults") if isinstance(doc.get("role_defaults"), dict) else {}
    for role_name, role_cfg in role_defaults.items():
        role = str(role_name or "").strip().lower()
        if not role or not isinstance(role_cfg, dict):
            continue
        pinned = role_cfg.get("pinned") if isinstance(role_cfg.get("pinned"), list) else []
        for item in pinned:
            cid = str(item or "").strip()
            if cid:
                role_pinned.setdefault(role, set()).add(cid)

    compiled["capability_levels"] = capability_levels
    compiled["skill_source_levels"] = skill_source_levels
    compiled["role_pinned"] = role_pinned
    compiled["curated_mcp_entries"] = curated_mcp_entries
    compiled["curated_skill_entries"] = curated_skill_entries
    return compiled


def _allowlist_policy() -> Dict[str, Any]:
    with _POLICY_LOCK:
        snapshot = _allowlist_effective_snapshot()
        key = str(snapshot.get("revision") or "")
        cached = _POLICY_CACHE.get("compiled")
        if _POLICY_CACHE.get("key") == key and isinstance(cached, dict):
            return cached

        error = ""
        compiled = _policy_default_compiled()
        overlay_error = str(snapshot.get("overlay_error") or "").strip()
        if overlay_error:
            error = overlay_error
        try:
            compiled = _compile_allowlist_policy(snapshot.get("effective"))
        except Exception as e:
            error = str(e)
            compiled = _policy_default_compiled()

        _POLICY_CACHE["key"] = key
        _POLICY_CACHE["compiled"] = compiled
        _POLICY_CACHE["source"] = (
            f"{snapshot.get('default_source')};overlay={snapshot.get('overlay_source') or '<none>'}"
        )
        _POLICY_CACHE["error"] = error
        return compiled


def _allowlist_validate_overlay_doc(overlay_doc: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any], Dict[str, Any], str]:
    default_doc, _ = _load_allowlist_default_doc()
    effective_doc = _merge_allowlist_docs(default_doc, overlay_doc)
    try:
        _compile_allowlist_policy(effective_doc)
    except Exception as e:
        return False, str(e), default_doc, effective_doc, ""
    revision_payload = json.dumps(
        {"default": default_doc, "overlay": overlay_doc},
        ensure_ascii=False,
        sort_keys=True,
    )
    revision = hashlib.sha1(revision_payload.encode("utf-8")).hexdigest()
    return True, "", default_doc, effective_doc, revision


def handle_capability_allowlist_get(args: Dict[str, Any]) -> DaemonResponse:
    try:
        with _POLICY_LOCK:
            snapshot = _allowlist_effective_snapshot()
            _ = _allowlist_policy()
        return DaemonResponse(
            ok=True,
            result={
                "default": snapshot.get("default") if isinstance(snapshot.get("default"), dict) else {},
                "overlay": snapshot.get("overlay") if isinstance(snapshot.get("overlay"), dict) else {},
                "effective": snapshot.get("effective") if isinstance(snapshot.get("effective"), dict) else {},
                "revision": str(snapshot.get("revision") or ""),
                "default_source": str(snapshot.get("default_source") or ""),
                "overlay_source": str(snapshot.get("overlay_source") or ""),
                "overlay_error": str(snapshot.get("overlay_error") or ""),
                "policy_source": str(_POLICY_CACHE.get("source") or ""),
                "policy_error": str(_POLICY_CACHE.get("error") or ""),
            },
        )
    except Exception as e:
        return _error("capability_allowlist_get_failed", str(e))


def handle_capability_allowlist_validate(args: Dict[str, Any]) -> DaemonResponse:
    mode = str(args.get("mode") or "patch").strip().lower()
    overlay_arg = args.get("overlay")
    patch_arg = args.get("patch")
    if mode not in {"patch", "replace"}:
        return _error("invalid_request", "mode must be patch or replace")
    if mode == "replace":
        if not isinstance(overlay_arg, dict):
            return _error("invalid_request", "overlay must be an object when mode=replace")
        overlay_next = dict(overlay_arg)
    else:
        if not isinstance(patch_arg, dict):
            return _error("invalid_request", "patch must be an object when mode=patch")
        with _POLICY_LOCK:
            snapshot = _allowlist_effective_snapshot()
            overlay_cur = snapshot.get("overlay") if isinstance(snapshot.get("overlay"), dict) else {}
        overlay_next = _merge_allowlist_docs(overlay_cur, patch_arg)

    try:
        valid, reason, default_doc, effective_doc, revision = _allowlist_validate_overlay_doc(overlay_next)
    except Exception as e:
        return _error("capability_allowlist_validate_failed", str(e))
    return DaemonResponse(
        ok=True,
        result={
            "valid": bool(valid),
            "reason": str(reason or ""),
            "default": default_doc,
            "overlay": overlay_next,
            "effective": effective_doc,
            "revision": str(revision or ""),
        },
    )


def handle_capability_allowlist_update(args: Dict[str, Any]) -> DaemonResponse:
    by = str(args.get("by") or "user").strip() or "user"
    mode = str(args.get("mode") or "patch").strip().lower()
    expected_revision = str(args.get("expected_revision") or "").strip()
    overlay_arg = args.get("overlay")
    patch_arg = args.get("patch")
    if by != "user":
        return _error("permission_denied", "only user can update capability allowlist overlay")
    if mode not in {"patch", "replace"}:
        return _error("invalid_request", "mode must be patch or replace")

    try:
        with _POLICY_LOCK:
            snapshot = _allowlist_effective_snapshot()
            current_revision = str(snapshot.get("revision") or "")
            if expected_revision and expected_revision != current_revision:
                return _error(
                    "allowlist_revision_mismatch",
                    "expected_revision does not match current revision",
                    details={"expected_revision": expected_revision, "current_revision": current_revision},
                )
            overlay_cur = snapshot.get("overlay") if isinstance(snapshot.get("overlay"), dict) else {}
            if mode == "replace":
                if not isinstance(overlay_arg, dict):
                    return _error("invalid_request", "overlay must be an object when mode=replace")
                overlay_next = dict(overlay_arg)
            else:
                if not isinstance(patch_arg, dict):
                    return _error("invalid_request", "patch must be an object when mode=patch")
                overlay_next = _merge_allowlist_docs(overlay_cur, patch_arg)

            valid, reason, default_doc, effective_doc, revision = _allowlist_validate_overlay_doc(overlay_next)
            if not valid:
                return _error(
                    "allowlist_validation_failed",
                    reason or "overlay validation failed",
                    details={"current_revision": current_revision},
                )
            _write_allowlist_overlay_doc(overlay_next)
            _clear_policy_cache()
            policy_compiled = _allowlist_policy()
            _ = policy_compiled  # cache warm-up for deterministic post-update behavior
    except Exception as e:
        return _error("capability_allowlist_update_failed", str(e))

    return DaemonResponse(
        ok=True,
        result={
            "updated": True,
            "revision": str(revision or ""),
            "default": default_doc,
            "overlay": overlay_next,
            "effective": effective_doc,
            "policy_source": str(_POLICY_CACHE.get("source") or ""),
            "policy_error": str(_POLICY_CACHE.get("error") or ""),
        },
    )


def handle_capability_allowlist_reset(args: Dict[str, Any]) -> DaemonResponse:
    by = str(args.get("by") or "user").strip() or "user"
    if by != "user":
        return _error("permission_denied", "only user can reset capability allowlist overlay")
    path = _allowlist_user_overlay_path()
    removed = False
    try:
        with _POLICY_LOCK:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
                    removed = True
            except Exception:
                removed = False
            _clear_policy_cache()
            snapshot = _allowlist_effective_snapshot()
            _ = _allowlist_policy()
    except Exception as e:
        return _error("capability_allowlist_reset_failed", str(e))
    return DaemonResponse(
        ok=True,
        result={
            "reset": True,
            "removed_overlay_file": bool(removed),
            "revision": str(snapshot.get("revision") or ""),
            "default": snapshot.get("default") if isinstance(snapshot.get("default"), dict) else {},
            "overlay": snapshot.get("overlay") if isinstance(snapshot.get("overlay"), dict) else {},
            "effective": snapshot.get("effective") if isinstance(snapshot.get("effective"), dict) else {},
            "default_source": str(snapshot.get("default_source") or ""),
            "overlay_source": str(snapshot.get("overlay_source") or ""),
            "overlay_error": str(snapshot.get("overlay_error") or ""),
            "policy_source": str(_POLICY_CACHE.get("source") or ""),
            "policy_error": str(_POLICY_CACHE.get("error") or ""),
        },
    )


def _resolve_actor_role(group: Any, actor_id: str) -> str:
    aid = str(actor_id or "").strip()
    if not aid or aid == "user":
        return ""
    try:
        return str(get_effective_role(group, aid) or "").strip().lower()
    except Exception:
        return ""


def _effective_policy_level(
    policy: Dict[str, Any],
    *,
    capability_id: str,
    kind: str,
    source_id: str,
    actor_role: str = "",
) -> str:
    cid = str(capability_id or "").strip()
    source = str(source_id or "").strip()
    kind_norm = str(kind or "").strip().lower()
    role = str(actor_role or "").strip().lower()

    source_levels = policy.get("source_levels") if isinstance(policy.get("source_levels"), dict) else {}
    capability_levels = policy.get("capability_levels") if isinstance(policy.get("capability_levels"), dict) else {}
    skill_source_levels = (
        policy.get("skill_source_levels") if isinstance(policy.get("skill_source_levels"), dict) else {}
    )
    role_pinned = policy.get("role_pinned") if isinstance(policy.get("role_pinned"), dict) else {}

    level = _normalize_policy_level(source_levels.get(source), default=_LEVEL_MOUNTED if not source else _LEVEL_INDEXED)
    if kind_norm == "skill":
        level = _normalize_policy_level(skill_source_levels.get(source, level), default=level)
    if cid and cid in capability_levels:
        level = _normalize_policy_level(capability_levels.get(cid), default=level)
    if role:
        role_caps = role_pinned.get(role)
        if isinstance(role_caps, set) and cid in role_caps:
            level = _LEVEL_PINNED
        elif isinstance(role_caps, list) and cid in {str(x or "").strip() for x in role_caps}:
            level = _LEVEL_PINNED
    return level


def _display_name_from_capability_id(capability_id: str) -> str:
    cid = str(capability_id or "").strip()
    if not cid:
        return "capability"
    if cid.startswith("skill:"):
        token = cid.split(":")[-1]
    elif cid.startswith("mcp:"):
        token = cid.split(":", 1)[1]
        if "/" in token:
            token = token.rsplit("/", 1)[-1]
    else:
        token = cid
    token = token.replace("_", " ").replace("-", " ").strip()
    return token or cid


def _curated_install_metadata(install_mode_preference: str) -> Tuple[str, Dict[str, Any]]:
    pref = str(install_mode_preference or "").strip().lower()
    if pref == "remote_only":
        return "remote_only", {"transport": "http", "url": ""}
    if pref == "package:npm":
        return "package", {"registry_type": "npm", "runtime_hint": "npx", "identifier": "", "version": ""}
    if pref.startswith("package:"):
        return "package", {"registry_type": pref.split(":", 1)[1], "runtime_hint": "", "identifier": "", "version": ""}
    return "", {}


def _build_curated_records_from_policy(policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    now_iso = utc_now_iso()
    out: Dict[str, Dict[str, Any]] = {}
    mcp_rows = policy.get("curated_mcp_entries") if isinstance(policy.get("curated_mcp_entries"), list) else []
    for raw in mcp_rows:
        if not isinstance(raw, dict):
            continue
        cap_id = str(raw.get("capability_id") or "").strip()
        if not cap_id.startswith("mcp:"):
            continue
        level = _normalize_policy_level(raw.get("level"), default=_LEVEL_MOUNTED)
        trust = str(raw.get("trust") or "").strip().lower()
        notes = str(raw.get("notes") or "").strip()
        risk_tags_raw = raw.get("risk_tags")
        risk_tags = [str(x).strip() for x in risk_tags_raw if str(x).strip()] if isinstance(risk_tags_raw, list) else []
        required_secrets_raw = raw.get("required_secrets")
        required_secrets = (
            [str(x).strip() for x in required_secrets_raw if str(x).strip()]
            if isinstance(required_secrets_raw, list)
            else []
        )
        install_mode, install_spec = _curated_install_metadata(str(raw.get("install_mode_preference") or ""))
        supported, unsupported_reason = _supported_external_install_record(
            {"install_mode": install_mode, "install_spec": install_spec}
        )
        hydration_needed = _needs_registry_hydration(
            cap_id,
            {"capability_id": cap_id, "kind": "mcp_toolpack", "install_mode": install_mode, "install_spec": install_spec},
        )
        qualification = _QUAL_QUALIFIED if (supported or hydration_needed) else _QUAL_UNAVAILABLE
        reasons: List[str] = []
        if level == _LEVEL_INDEXED:
            reasons.append("curated_indexed_default")
        if risk_tags:
            reasons.append("risk_tags_present")
        if unsupported_reason:
            reasons.append(unsupported_reason)
        out[cap_id] = {
            "capability_id": cap_id,
            "kind": "mcp_toolpack",
            "name": _display_name_from_capability_id(cap_id),
            "description_short": notes or f"Curated MCP capability {cap_id}",
            "tags": ["mcp", "external", "curated", *risk_tags],
            "source_id": "mcp_registry_official",
            "source_tier": "tier1",
            "source_uri": "",
            "source_record_id": cap_id.split(":", 1)[1],
            "source_record_version": "",
            "updated_at_source": now_iso,
            "last_synced_at": now_iso,
            "sync_state": "curated",
            "install_mode": install_mode,
            "install_spec": install_spec,
            "requirements": {"required_secrets": required_secrets} if required_secrets else {},
            "license": "",
            "trust_tier": trust or "tier1",
            "qualification_status": qualification,
            "qualification_reasons": reasons,
            "health_status": "curated",
            "enable_supported": bool(supported or hydration_needed),
        }

    skill_rows = policy.get("curated_skill_entries") if isinstance(policy.get("curated_skill_entries"), list) else []
    for raw in skill_rows:
        if not isinstance(raw, dict):
            continue
        cap_id = str(raw.get("capability_id") or "").strip()
        if not cap_id.startswith("skill:"):
            continue
        name = str(raw.get("name") or "").strip() or _display_name_from_capability_id(cap_id).replace(" ", "-")
        level = _normalize_policy_level(raw.get("level"), default=_LEVEL_MOUNTED)
        trust = str(raw.get("trust") or "").strip().lower()
        notes = str(raw.get("notes") or "").strip()
        source_id = str(raw.get("source_id") or "anthropic_skills").strip() or "anthropic_skills"
        source_uri = str(raw.get("source_uri") or "").strip()
        description_short = str(raw.get("description_short") or "").strip()
        license_text = str(raw.get("license") or "").strip()
        tags_raw = raw.get("tags")
        tags = [str(x).strip() for x in tags_raw if str(x).strip()] if isinstance(tags_raw, list) else []
        if source_id == "anthropic_skills" and "anthropic" not in {t.lower() for t in tags}:
            tags.append("anthropic")
        requires_raw = raw.get("requires_capabilities")
        requires_capabilities = (
            [str(x).strip() for x in requires_raw if str(x).strip()]
            if isinstance(requires_raw, list)
            else []
        )
        capsule_text = str(raw.get("capsule_text") or "").strip()
        qualification = str(raw.get("qualification_status") or "").strip().lower()
        if qualification not in _QUAL_STATES:
            qualification = _QUAL_QUALIFIED
        reasons_raw = raw.get("qualification_reasons")
        reasons = (
            [str(x).strip() for x in reasons_raw if str(x).strip()]
            if isinstance(reasons_raw, list)
            else []
        )
        if not reasons and qualification != _QUAL_QUALIFIED:
            reasons = [f"curated_{qualification}"]
        if not source_uri and source_id == "anthropic_skills":
            source_uri = f"https://github.com/anthropics/skills/tree/main/skills/{name}"
        if not description_short:
            description_short = notes or f"Curated skill {name}"
        if not capsule_text:
            capsule_text = f"Skill: {name}\nSummary: {description_short}".strip()
        out[cap_id] = {
            "capability_id": cap_id,
            "kind": "skill",
            "name": name,
            "description_short": description_short,
            "tags": ["skill", "external", "curated", *tags],
            "source_id": source_id,
            "source_tier": "tier1" if source_id == "anthropic_skills" else "tier2",
            "source_uri": source_uri,
            "source_record_id": name,
            "source_record_version": "",
            "updated_at_source": now_iso,
            "last_synced_at": now_iso,
            "sync_state": "curated",
            "install_mode": "builtin",
            "install_spec": {},
            "requirements": {},
            "license": license_text,
            "trust_tier": trust or ("tier1" if source_id == "anthropic_skills" else "tier2"),
            "qualification_status": qualification,
            "qualification_reasons": reasons,
            "health_status": "curated",
            "enable_supported": qualification != _QUAL_BLOCKED,
            "capsule_text": capsule_text,
            "requires_capabilities": requires_capabilities,
        }
    return out


def _ensure_curated_catalog_records(catalog_doc: Dict[str, Any], *, policy: Dict[str, Any]) -> bool:
    records = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
    if not isinstance(records, dict):
        records = {}
    curated = _build_curated_records_from_policy(policy)
    if not curated:
        return False

    changed = False
    touched_sources: set[str] = set()
    for cap_id, rec in curated.items():
        existing = records.get(cap_id) if isinstance(records.get(cap_id), dict) else None
        if isinstance(existing, dict):
            if existing == rec:
                continue
            touched_sources.add(str(existing.get("source_id") or "").strip())
            records[cap_id] = rec
            touched_sources.add(str(rec.get("source_id") or "").strip())
            changed = True
            continue
        records[cap_id] = rec
        touched_sources.add(str(rec.get("source_id") or "").strip())
        changed = True
    if not changed:
        return False

    catalog_doc["records"] = records
    sources = catalog_doc.get("sources") if isinstance(catalog_doc.get("sources"), dict) else {}
    now_iso = utc_now_iso()
    for source_id in touched_sources:
        if not source_id:
            continue
        state = sources.get(source_id) if isinstance(sources.get(source_id), dict) else _source_state_template("never")
        if str(state.get("sync_state") or "").strip() in {"", "never", "disabled"}:
            state["sync_state"] = "curated"
        if not str(state.get("last_synced_at") or "").strip():
            state["last_synced_at"] = now_iso
        state["error"] = ""
        state["staleness_seconds"] = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
        sources[source_id] = state
    catalog_doc["sources"] = sources
    _refresh_source_record_counts(catalog_doc)
    return True


def _install_spec_ready(rec: Dict[str, Any]) -> bool:
    install_mode = str(rec.get("install_mode") or "").strip()
    spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    if install_mode == "remote_only":
        return bool(str(spec.get("url") or "").strip())
    if install_mode == "package":
        return bool(str(spec.get("identifier") or "").strip())
    return False


def _needs_registry_hydration(capability_id: str, rec: Dict[str, Any]) -> bool:
    cap_id = str(capability_id or "").strip()
    if not cap_id.startswith("mcp:"):
        return False
    if str(rec.get("kind") or "").strip().lower() != "mcp_toolpack":
        return False
    return not _install_spec_ready(rec)


def _merge_registry_install_into_record(rec: Dict[str, Any], fetched: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(rec)
    for key in (
        "install_mode",
        "install_spec",
        "source_uri",
        "source_record_id",
        "source_record_version",
        "updated_at_source",
        "last_synced_at",
        "health_status",
    ):
        if key in fetched:
            merged[key] = fetched.get(key)
    if not str(merged.get("name") or "").strip():
        merged["name"] = str(fetched.get("name") or "")
    if not str(merged.get("description_short") or "").strip():
        merged["description_short"] = str(fetched.get("description_short") or "")
    if not isinstance(merged.get("tags"), list) or not merged.get("tags"):
        merged["tags"] = list(fetched.get("tags") or [])
    qualification = str(merged.get("qualification_status") or "").strip().lower()
    merged["enable_supported"] = bool(_install_spec_ready(merged) and qualification != _QUAL_BLOCKED)
    return merged


def _github_headers() -> Dict[str, str]:
    headers = {
        "User-Agent": "cccc-capability-sync/1.0",
        "Accept": "application/vnd.github+json",
    }
    token = str(os.environ.get("CCCC_CAPABILITY_GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _catalog_staleness_seconds(last_synced_at: str) -> Optional[int]:
    dt = parse_utc_iso(str(last_synced_at or ""))
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0, int((now - dt).total_seconds()))


def _sanitize_tool_token(raw: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    return token or "tool"


def _sanitize_skill_id_token(raw: str, *, default: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
    return token or default


def _build_synthetic_tool_name(capability_id: str, real_tool_name: str, *, used: set[str]) -> str:
    cap_hash = hashlib.sha1(str(capability_id or "").encode("utf-8")).hexdigest()[:8]
    base = f"cccc_ext_{cap_hash}_{_sanitize_tool_token(real_tool_name)}"
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def _normalize_mcp_input_schema(schema: Any) -> Dict[str, Any]:
    if isinstance(schema, dict) and str(schema.get("type") or "").strip():
        return dict(schema)
    return {"type": "object", "properties": {}, "required": []}


def _normalize_discovered_tools(capability_id: str, tools: Any) -> List[Dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    out: List[Dict[str, Any]] = []
    used: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            continue
        real_name = str(item.get("name") or "").strip()
        if not real_name:
            continue
        out.append(
            {
                "name": _build_synthetic_tool_name(capability_id, real_name, used=used),
                "real_tool_name": real_name,
                "description": str(item.get("description") or "").strip(),
                "inputSchema": _normalize_mcp_input_schema(item.get("inputSchema")),
            }
        )
    return out


def _normalize_registry_argument_entries(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        arg_type = str(item.get("type") or "positional").strip().lower()
        if arg_type not in {"positional", "named"}:
            arg_type = "positional"
        value = str(item.get("value") or "").strip()
        name = str(item.get("name") or "").strip()
        if arg_type == "named":
            if not name:
                continue
            out.append({"type": "named", "name": name, "value": value})
            continue
        if not value:
            continue
        out.append({"type": "positional", "value": value})
    return out


def _normalize_registry_env_names(raw: Any, *, required_only: bool) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        if required_only and (not bool(item.get("isRequired"))):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in out:
            continue
        out.append(name)
    return out


def _extract_required_env_from_runtime_arguments(raw: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "named":
            continue
        if str(item.get("name") or "").strip() not in {"-e", "--env"}:
            continue
        value = str(item.get("value") or "").strip()
        match = _ENV_FORWARD_TEMPLATE_RE.fullmatch(value)
        if not match:
            continue
        env_name = str(match.group(1) or "").strip()
        if not env_name:
            continue
        variables = item.get("variables") if isinstance(item.get("variables"), dict) else {}
        # Only treat as required when variable metadata explicitly marks it required.
        is_required = False
        for var_cfg in variables.values():
            if isinstance(var_cfg, dict) and bool(var_cfg.get("isRequired")):
                is_required = True
                break
        if is_required and env_name not in out:
            out.append(env_name)
    return out


def _literal_registry_argument_tokens(entries: List[Dict[str, str]]) -> Tuple[Optional[List[str]], str]:
    tokens: List[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        arg_type = str(item.get("type") or "positional").strip().lower()
        if arg_type == "named":
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name:
                continue
            tokens.append(name)
            if value:
                if _ARG_TEMPLATE_RE.search(value):
                    return None, "unsupported_argument_template"
                tokens.append(value)
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if _ARG_TEMPLATE_RE.search(value):
            return None, "unsupported_argument_template"
        tokens.append(value)
    return tokens, ""


def _oci_runtime_argument_tokens(entries: List[Dict[str, str]]) -> Tuple[Optional[List[str]], List[str], str]:
    tokens: List[str] = []
    forwarded_envs: List[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        arg_type = str(item.get("type") or "positional").strip().lower()
        if arg_type == "named":
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name:
                continue
            if value:
                match = _ENV_FORWARD_TEMPLATE_RE.fullmatch(value)
                if name in {"-e", "--env"} and match:
                    env_name = str(match.group(1) or "").strip()
                    if not env_name:
                        continue
                    tokens.extend([name, env_name])
                    if env_name not in forwarded_envs:
                        forwarded_envs.append(env_name)
                    continue
                if _ARG_TEMPLATE_RE.search(value):
                    return None, [], "unsupported_runtime_argument_template"
                tokens.extend([name, value])
            else:
                tokens.append(name)
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if _ARG_TEMPLATE_RE.search(value):
            return None, [], "unsupported_runtime_argument_template"
        tokens.append(value)
    return tokens, forwarded_envs, ""


def _required_environment_names(rec: Dict[str, Any]) -> List[str]:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    raw = install_spec.get("required_env")
    out: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            name = str(item or "").strip()
            if name and name not in out:
                out.append(name)
    return out


def _missing_required_environment_names(rec: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for name in _required_environment_names(rec):
        value = str(os.environ.get(name) or "").strip()
        if value:
            continue
        missing.append(name)
    return missing


def _normalize_registry_type_token(raw: str) -> str:
    token = str(raw or "").strip().lower()
    if token in {"", "npm", "node", "npmjs"}:
        return "npm"
    if token in {"pypi", "python", "pip", "pipx", "uvx", "uv"}:
        return "pypi"
    if token in {"oci", "docker", "container", "podman", "ghcr", "ghcr.io"}:
        return "oci"
    return token


def _effective_registry_type(install_spec: Dict[str, Any]) -> str:
    spec = install_spec if isinstance(install_spec, dict) else {}
    declared = _normalize_registry_type_token(str(spec.get("registry_type") or ""))
    if declared in {"npm", "pypi", "oci"}:
        return declared
    runtime_hint = _normalize_registry_type_token(str(spec.get("runtime_hint") or ""))
    if runtime_hint in {"pypi", "oci"}:
        return runtime_hint
    identifier = str(spec.get("identifier") or "").strip().lower()
    if identifier.startswith(("ghcr.io/", "docker.io/", "quay.io/")):
        return "oci"
    return declared or "npm"


def _tool_name_aliases(raw: str) -> List[str]:
    name = str(raw or "").strip()
    if not name:
        return []
    out: List[str] = []
    for token in (name, name.replace("-", "_"), name.replace("_", "-")):
        if token and token not in out:
            out.append(token)
    return out


def _is_unknown_tool_error_message(msg: str) -> bool:
    text = str(msg or "").strip().lower()
    return "unknown tool" in text or "tool not found" in text


def _npx_package_command(rec: Dict[str, Any]) -> Tuple[Optional[List[str]], str]:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    identifier = str(install_spec.get("identifier") or "").strip()
    if not identifier:
        return None, "missing_package_identifier"
    version = str(install_spec.get("version") or "").strip()
    pkg = identifier
    if version and "@" in identifier[1:]:
        pkg = identifier
    elif version:
        pkg = f"{identifier}@{version}"
    package_args = _normalize_registry_argument_entries(install_spec.get("package_arguments"))
    package_tokens, package_reason = _literal_registry_argument_tokens(package_args)
    if package_tokens is None:
        return None, package_reason or "unsupported_package_arguments"
    runtime_hint_raw = str(install_spec.get("runtime_hint") or "").strip().lower()
    if runtime_hint_raw and runtime_hint_raw not in {"auto", "npx"}:
        runtime_hint = _normalize_registry_type_token(runtime_hint_raw)
        if runtime_hint != "npm":
            return None, "unsupported_runtime_hint"
    return ["npx", "-y", pkg, *(package_tokens or [])], ""


def _pypi_package_commands(rec: Dict[str, Any]) -> Tuple[List[List[str]], str]:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    identifier = str(install_spec.get("identifier") or "").strip()
    if not identifier:
        return [], "missing_package_identifier"
    version = str(install_spec.get("version") or "").strip()
    base_spec = identifier
    if version and "@" in identifier[1:]:
        base_spec = identifier
    elif version:
        base_spec = f"{identifier}@{version}"

    runtime_entries = _normalize_registry_argument_entries(install_spec.get("runtime_arguments"))
    package_entries = _normalize_registry_argument_entries(install_spec.get("package_arguments"))
    runtime_tokens, runtime_reason = _literal_registry_argument_tokens(runtime_entries)
    if runtime_tokens is None:
        return [], runtime_reason or "unsupported_runtime_arguments"
    package_tokens, package_reason = _literal_registry_argument_tokens(package_entries)
    if package_tokens is None:
        return [], package_reason or "unsupported_package_arguments"

    runtime_hint = str(install_spec.get("runtime_hint") or "").strip().lower()
    if runtime_hint and runtime_hint not in {"uvx", "pipx", "python", "auto"}:
        return [], "unsupported_runtime_hint"

    # Prefer uvx for modern pypi MCP servers; fall back to pipx where needed.
    runners: List[str] = []
    if runtime_hint in {"", "uvx", "python", "auto"}:
        runners.append("uvx")
    if runtime_hint in {"", "pipx", "python", "auto"}:
        runners.append("pipx")

    commands: List[List[str]] = []
    for runner in runners:
        if runner == "uvx":
            cmd = ["uvx", *((runtime_tokens or [base_spec]))]
            cmd.extend(package_tokens or [])
            commands.append(cmd)
            continue
        # pipx does not understand runtimeArguments shape; use --spec + entrypoint fallback.
        cmd = ["pipx", "run", "--spec", base_spec, identifier]
        cmd.extend(package_tokens or [])
        commands.append(cmd)
    return commands, ""


def _oci_package_commands(rec: Dict[str, Any]) -> Tuple[List[List[str]], str]:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    image = str(install_spec.get("identifier") or "").strip()
    if not image:
        return [], "missing_package_identifier"

    runtime_entries = _normalize_registry_argument_entries(install_spec.get("runtime_arguments"))
    package_entries = _normalize_registry_argument_entries(install_spec.get("package_arguments"))
    runtime_tokens, forwarded_envs, runtime_reason = _oci_runtime_argument_tokens(runtime_entries)
    if runtime_tokens is None:
        return [], runtime_reason or "unsupported_runtime_arguments"
    package_tokens, package_reason = _literal_registry_argument_tokens(package_entries)
    if package_tokens is None:
        return [], package_reason or "unsupported_package_arguments"

    runtime_hint = str(install_spec.get("runtime_hint") or "").strip().lower()
    if runtime_hint and runtime_hint not in {"docker", "podman", "container", "oci", "auto"}:
        return [], "unsupported_runtime_hint"
    engines: List[str] = []
    if runtime_hint in {"podman"}:
        engines.append("podman")
    elif runtime_hint in {"docker"}:
        engines.append("docker")
    else:
        engines.extend(["docker", "podman"])

    required_env = _required_environment_names(rec)
    env_names = install_spec.get("env_names") if isinstance(install_spec.get("env_names"), list) else []
    all_env_forward: List[str] = []
    for token in [*forwarded_envs, *required_env, *[str(x).strip() for x in env_names if str(x).strip()]]:
        if token and token not in all_env_forward:
            all_env_forward.append(token)

    commands: List[List[str]] = []
    for engine in engines:
        cmd = [engine, "run", "-i", "--rm"]
        for env_name in all_env_forward:
            cmd.extend(["-e", env_name])
        cmd.extend(runtime_tokens or [])
        cmd.append(image)
        cmd.extend(package_tokens or [])
        commands.append(cmd)
    return commands, ""


def _package_stdio_command_candidates(rec: Dict[str, Any]) -> Tuple[List[List[str]], str]:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    registry_type = _effective_registry_type(install_spec)
    if registry_type == "npm":
        command, reason = _npx_package_command(rec)
        if not command:
            return [], reason
        commands: List[List[str]] = [command]
        identifier = str(install_spec.get("identifier") or "").strip()
        version = str(install_spec.get("version") or "").strip()
        if identifier and version:
            fallback_spec = dict(install_spec)
            fallback_spec["version"] = ""
            fallback_rec = dict(rec)
            fallback_rec["install_spec"] = fallback_spec
            fallback_command, fallback_reason = _npx_package_command(fallback_rec)
            if fallback_command and fallback_command not in commands:
                commands.append(fallback_command)
            elif (not fallback_command) and fallback_reason:
                return commands, reason
        return commands, reason
    if registry_type == "pypi":
        return _pypi_package_commands(rec)
    if registry_type == "oci":
        return _oci_package_commands(rec)
    return [], f"unsupported_registry_type:{registry_type}"


def _choose_available_command(commands: List[List[str]]) -> List[List[str]]:
    if not isinstance(commands, list):
        return []
    if len(commands) <= 1:
        return commands
    available: List[List[str]] = []
    unavailable: List[List[str]] = []
    for cmd in commands:
        if not isinstance(cmd, list) or not cmd:
            continue
        exe = str(cmd[0] or "").strip()
        if exe and shutil.which(exe):
            available.append(cmd)
        else:
            unavailable.append(cmd)
    return [*available, *unavailable]


def _installer_label_for_command(rec: Dict[str, Any], command: List[str]) -> str:
    install_spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    registry_type = _effective_registry_type(install_spec)
    exe = str((command or [None])[0] or "").strip().lower()
    if registry_type == "npm":
        return "npm_npx"
    if registry_type == "pypi":
        if exe == "pipx":
            return "pypi_pipx"
        return "pypi_uvx"
    if registry_type == "oci":
        if exe == "podman":
            return "oci_podman"
        return "oci_docker"
    return f"{registry_type}_stdio"


def _stdio_mcp_roundtrip(
    command: List[str],
    requests: List[Dict[str, Any]],
    *,
    timeout_s: float,
    env_override: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    env: Optional[Dict[str, str]] = None
    if isinstance(env_override, dict):
        merged = dict(os.environ)
        for key, value in env_override.items():
            k = str(key or "").strip()
            if not k:
                continue
            merged[k] = str(value or "")
        env = merged
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    input_blob = "\n".join(json.dumps(req, ensure_ascii=False) for req in requests) + "\n"
    try:
        out, err = proc.communicate(input=input_blob, timeout=max(2.0, float(timeout_s)))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError("stdio mcp request timed out")
    if proc.returncode not in {0, None} and not out.strip():
        raise RuntimeError(f"stdio mcp exited with code {proc.returncode}: {err.strip()}")
    responses: List[Dict[str, Any]] = []
    for line in str(out or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if isinstance(item, dict):
            responses.append(item)
    return responses


def _extract_jsonrpc_result(
    responses: List[Dict[str, Any]],
    *,
    req_id: int,
    operation: str,
) -> Dict[str, Any]:
    for item in responses:
        if int(item.get("id") or -1) != int(req_id):
            continue
        err = item.get("error")
        if isinstance(err, dict):
            raise RuntimeError(f"{operation} failed: {str(err.get('message') or 'unknown error')}")
        result = item.get("result")
        return result if isinstance(result, dict) else {}
    raise RuntimeError(f"{operation} failed: missing response")


def _http_jsonrpc_request(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout_s: float,
    session_id: str = "",
) -> Tuple[Dict[str, Any], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if session_id:
        req.add_header("Mcp-Session-Id", session_id)
    with urlopen(req, timeout=max(2.0, float(timeout_s))) as resp:
        payload_text = resp.read().decode("utf-8", errors="replace")
        out_session = str(resp.headers.get("Mcp-Session-Id") or session_id or "").strip()
    data = json.loads(payload_text) if payload_text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("remote mcp response is not JSON object")
    return data, out_session


def _remote_mcp_call(url: str, method: str, params: Dict[str, Any], *, timeout_s: float) -> Dict[str, Any]:
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cccc-capability-runtime", "version": "1.0"},
        },
    }
    init_resp, session_id = _http_jsonrpc_request(url, init_req, timeout_s=timeout_s)
    if isinstance(init_resp.get("error"), dict):
        raise RuntimeError(str((init_resp.get("error") or {}).get("message") or "remote initialize failed"))
    call_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": str(method or ""),
        "params": params if isinstance(params, dict) else {},
    }
    call_resp, _ = _http_jsonrpc_request(url, call_req, timeout_s=timeout_s, session_id=session_id)
    if isinstance(call_resp.get("error"), dict):
        raise RuntimeError(str((call_resp.get("error") or {}).get("message") or f"remote {method} failed"))
    result = call_resp.get("result")
    return result if isinstance(result, dict) else {}


def _supported_external_install_record(rec: Dict[str, Any]) -> Tuple[bool, str]:
    install_mode = str(rec.get("install_mode") or "").strip()
    spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    if install_mode == "remote_only":
        transport = str(spec.get("transport") or "").strip().lower()
        if transport in {"", "streamable-http", "http", "sse"}:
            if str(spec.get("url") or "").strip():
                return True, ""
            return False, "missing_remote_url"
        return False, f"unsupported_remote_transport:{transport or 'unknown'}"
    if install_mode == "package":
        commands, reason = _package_stdio_command_candidates(rec)
        if not commands:
            return False, (reason or "unsupported_runtime_hint")
        return True, ""
    return False, f"unsupported_install_mode:{install_mode or 'unknown'}"


def _record_enable_supported(rec: Dict[str, Any], *, capability_id: str = "") -> bool:
    raw = rec.get("enable_supported")
    if isinstance(raw, bool):
        return raw
    cap_id = str(capability_id or rec.get("capability_id") or "").strip()
    kind = str(rec.get("kind") or "").strip().lower()
    qualification = str(rec.get("qualification_status") or "").strip().lower()
    if qualification == _QUAL_BLOCKED:
        return False
    if cap_id.startswith("pack:"):
        return True
    if kind == "skill":
        return qualification != _QUAL_BLOCKED
    if _needs_registry_hydration(cap_id, rec):
        return True
    supported, _ = _supported_external_install_record(rec)
    return bool(supported)


def _external_artifact_cache_key(rec: Dict[str, Any], *, capability_id: str) -> str:
    install_mode = str(rec.get("install_mode") or "").strip().lower()
    spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
    if install_mode == "remote_only":
        url = str(spec.get("url") or "").strip()
        if url:
            return f"remote_only::{url}"
    if install_mode == "package":
        registry_type = _effective_registry_type(spec)
        identifier = str(spec.get("identifier") or "").strip()
        version = str(spec.get("version") or "").strip()
        runtime_hint = str(spec.get("runtime_hint") or "").strip().lower()
        runtime_args = spec.get("runtime_arguments") if isinstance(spec.get("runtime_arguments"), list) else []
        package_args = spec.get("package_arguments") if isinstance(spec.get("package_arguments"), list) else []
        args_digest = ""
        if runtime_args or package_args:
            try:
                args_payload = json.dumps(
                    {"runtime": runtime_args, "package": package_args},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                args_digest = hashlib.sha1(args_payload.encode("utf-8")).hexdigest()[:8]
            except Exception:
                args_digest = ""
        if identifier:
            return f"package::{registry_type}::{identifier}::{version}::{runtime_hint}::{args_digest}"
    return f"capability::{str(capability_id or '').strip()}"


def _external_artifact_id(rec: Dict[str, Any], *, capability_id: str) -> str:
    key = _external_artifact_cache_key(rec, capability_id=capability_id)
    return f"art_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def _is_package_probe_degradable_error(err: Exception) -> bool:
    if isinstance(err, TimeoutError):
        return True
    text = str(err or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "stdio mcp request timed out",
            "tools/list returned no tools",
            "tools/list failed: missing response",
            "missing response",
        )
    )


def _classify_external_install_error(err: Exception) -> Dict[str, Any]:
    message = str(err or "").strip()
    text = message.lower()
    out: Dict[str, Any] = {
        "code": "install_failed",
        "message": message,
        "retryable": False,
    }
    if not message:
        return out

    if message.startswith("missing_required_env:"):
        raw_names = [x.strip() for x in message.split(":", 1)[1].split(",")]
        names = [x for x in raw_names if x]
        out["code"] = "missing_required_env"
        out["required_env"] = names
        return out
    if message.startswith("unsupported_registry_type:"):
        out["code"] = "unsupported_registry_type"
        out["registry_type"] = message.split(":", 1)[1].strip()
        return out
    if message.startswith("unsupported_install_mode:"):
        out["code"] = "unsupported_install_mode"
        out["install_mode"] = message.split(":", 1)[1].strip()
        return out
    if message.startswith("unsupported_runtime_hint"):
        out["code"] = "unsupported_runtime_hint"
        return out
    if message.startswith("missing_package_identifier"):
        out["code"] = "missing_package_identifier"
        return out
    if message.startswith("missing_remote_url"):
        out["code"] = "missing_remote_url"
        return out
    if "probe_failed" in text:
        out["code"] = "probe_failed"
        out["retryable"] = True
        return out
    if isinstance(err, TimeoutError) or ("timed out" in text) or ("timeout" in text):
        out["code"] = "probe_timeout"
        out["retryable"] = True
        return out
    if isinstance(err, FileNotFoundError):
        out["code"] = "runtime_binary_missing"
        return out
    if "permission denied while trying to connect to the docker daemon socket" in text:
        out["code"] = "runtime_permission_denied"
        return out
    if "permission denied" in text and ("docker.sock" in text or "podman" in text):
        out["code"] = "runtime_permission_denied"
        return out
    if "cannot find module" in text or "module_not_found" in text:
        out["code"] = "runtime_dependency_missing"
        return out
    if "stdio mcp exited with code" in text:
        out["code"] = "runtime_start_failed"
        return out
    if any(token in text for token in ("name or service not known", "temporary failure in name resolution", "nodename nor servname provided")):
        out["code"] = "network_dns_failure"
        out["retryable"] = True
        return out
    if any(token in text for token in ("connection refused", "connection reset", "network is unreachable")):
        out["code"] = "network_unreachable"
        out["retryable"] = True
        return out
    if "http error 401" in text or "unauthorized" in text:
        out["code"] = "upstream_unauthorized"
        return out
    if "http error 403" in text or "forbidden" in text:
        out["code"] = "upstream_forbidden"
        return out
    return out


def _diagnostics_from_install_error(err: Exception) -> List[Dict[str, Any]]:
    info = _classify_external_install_error(err)
    code = str(info.get("code") or "install_failed")
    message = str(info.get("message") or str(err or "")).strip()
    diag: Dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": bool(info.get("retryable")),
    }
    required_env = info.get("required_env")
    if isinstance(required_env, list) and required_env:
        diag["required_env"] = [str(x).strip() for x in required_env if str(x).strip()]
    action_hints: List[str] = []
    if code == "missing_required_env":
        action_hints.append("set_required_env_then_retry")
    elif code == "runtime_binary_missing":
        action_hints.append("install_or_expose_runtime_binary_then_retry")
    elif code == "runtime_permission_denied":
        action_hints.append("grant_runtime_permission_then_retry")
    elif code in {"runtime_dependency_missing", "runtime_start_failed"}:
        action_hints.append("retry_with_safe_runtime_flags_or_different_version")
    elif code in {"probe_timeout", "network_dns_failure", "network_unreachable"}:
        action_hints.append("retry_or_fix_network_then_retry")
    if action_hints:
        diag["action_hints"] = action_hints
    return [diag]


def _artifact_entry_from_install(
    install: Dict[str, Any],
    *,
    artifact_id: str,
    install_key: str,
    capability_id: str,
) -> Dict[str, Any]:
    return {
        "artifact_id": str(artifact_id or "").strip(),
        "install_key": str(install_key or "").strip(),
        "state": str(install.get("state") or "").strip() or "unknown",
        "installer": str(install.get("installer") or "").strip(),
        "install_mode": str(install.get("install_mode") or "").strip(),
        "invoker": dict(install.get("invoker")) if isinstance(install.get("invoker"), dict) else {},
        "tools": list(install.get("tools") or []) if isinstance(install.get("tools"), list) else [],
        "last_error": str(install.get("last_error") or "").strip(),
        "last_error_code": str(install.get("last_error_code") or "").strip(),
        "updated_at": str(install.get("updated_at") or "").strip() or utc_now_iso(),
        "capability_ids": [str(capability_id or "").strip()] if str(capability_id or "").strip() else [],
    }


def _upsert_runtime_artifact_for_capability(
    runtime_doc: Dict[str, Any],
    *,
    artifact_id: str,
    capability_id: str,
    artifact_entry: Dict[str, Any],
) -> None:
    aid = str(artifact_id or "").strip()
    cid = str(capability_id or "").strip()
    if not aid or not cid:
        return
    artifacts = _runtime_artifacts(runtime_doc)
    row = dict(artifact_entry) if isinstance(artifact_entry, dict) else {}
    row["artifact_id"] = aid
    caps_raw = row.get("capability_ids")
    caps = [str(x).strip() for x in caps_raw if str(x).strip()] if isinstance(caps_raw, list) else []
    if cid not in caps:
        caps.append(cid)
    row["capability_ids"] = caps
    artifacts[aid] = row
    runtime_doc["artifacts"] = artifacts
    _set_runtime_capability_artifact(runtime_doc, capability_id=cid, artifact_id=aid)

def _install_external_capability(rec: Dict[str, Any], *, capability_id: str) -> Dict[str, Any]:
    install_mode = str(rec.get("install_mode") or "").strip()
    if install_mode == "remote_only":
        spec = rec.get("install_spec") if isinstance(rec.get("install_spec"), dict) else {}
        url = str(spec.get("url") or "").strip()
        if not url:
            raise ValueError("missing_remote_url")
        tools_result = _remote_mcp_call(url, "tools/list", {}, timeout_s=12.0)
        tools = _normalize_discovered_tools(capability_id, tools_result.get("tools"))
        if not tools:
            raise RuntimeError("remote tools/list returned no tools")
        return {
            "state": "installed",
            "installer": "remote_http",
            "install_mode": "remote_only",
            "invoker": {"type": "remote_http", "url": url},
            "tools": tools,
            "last_error": "",
            "updated_at": utc_now_iso(),
        }

    if install_mode == "package":
        commands, reason = _package_stdio_command_candidates(rec)
        if not commands:
            raise ValueError(reason or "unsupported_runtime_hint")
        missing_env = _missing_required_environment_names(rec)
        if missing_env:
            raise ValueError("missing_required_env:" + ",".join(sorted(set(missing_env))))
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cccc-capability-runtime", "version": "1.0"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        commands = _choose_available_command(commands)
        probe_timeout_s = float(max(5, min(_env_int("CCCC_CAPABILITY_PACKAGE_PROBE_TIMEOUT_SECONDS", 30), 120)))
        last_error: Optional[Exception] = None
        preferred_error: Optional[Exception] = None
        chosen_command: List[str] = []
        chosen_env: Dict[str, str] = {}
        tools: List[Dict[str, Any]] = []
        for command in commands:
            attempt_envs: List[Dict[str, str]] = [{}]
            exe = str((command or [""])[0] or "").strip().lower()
            if exe == "npx":
                attempt_envs.append(
                    {
                        "PUPPETEER_SKIP_DOWNLOAD": "1",
                        "PUPPETEER_SKIP_CHROMIUM_DOWNLOAD": "1",
                        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
                    }
                )
            for env_try in attempt_envs:
                try:
                    if env_try:
                        responses = _stdio_mcp_roundtrip(
                            command,
                            requests,
                            timeout_s=probe_timeout_s,
                            env_override=env_try,
                        )
                    else:
                        responses = _stdio_mcp_roundtrip(command, requests, timeout_s=probe_timeout_s)
                    tools_result = _extract_jsonrpc_result(responses, req_id=2, operation="tools/list")
                    tools = _normalize_discovered_tools(capability_id, tools_result.get("tools"))
                    if not tools:
                        raise RuntimeError("package tools/list returned no tools")
                    chosen_command = list(command)
                    chosen_env = dict(env_try)
                    break
                except FileNotFoundError as e:
                    last_error = e
                    if preferred_error is None:
                        preferred_error = e
                    continue
                except Exception as e:
                    last_error = e
                    preferred_error = e
                    continue
            if chosen_command:
                break
        if not chosen_command:
            effective_error = preferred_error if isinstance(preferred_error, Exception) else last_error
            if isinstance(effective_error, Exception) and commands and _is_package_probe_degradable_error(effective_error):
                fallback_command = list(commands[0])
                classified = _classify_external_install_error(effective_error)
                return {
                    "state": "installed_degraded",
                    "installer": _installer_label_for_command(rec, fallback_command),
                    "install_mode": "package",
                    "invoker": {"type": "package_stdio", "command": fallback_command},
                    "tools": [],
                    "last_error": str(effective_error),
                    "last_error_code": str(classified.get("code") or "probe_timeout"),
                    "retryable": bool(classified.get("retryable")),
                    "updated_at": utc_now_iso(),
                }
            if isinstance(effective_error, Exception):
                raise effective_error
            raise RuntimeError("package install failed: no runnable command candidate")
        invoker: Dict[str, Any] = {"type": "package_stdio", "command": chosen_command}
        if chosen_env:
            invoker["env"] = chosen_env
        return {
            "state": "installed",
            "installer": _installer_label_for_command(rec, chosen_command),
            "install_mode": "package",
            "invoker": invoker,
            "tools": tools,
            "last_error": "",
            "updated_at": utc_now_iso(),
        }

    raise ValueError(f"unsupported_install_mode:{install_mode or 'unknown'}")


def _invoke_installed_external_tool(
    install: Dict[str, Any],
    *,
    real_tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
    invoker_type = str(invoker.get("type") or "").strip()
    if invoker_type == "remote_http":
        url = str(invoker.get("url") or "").strip()
        if not url:
            raise ValueError("missing_remote_url")
        return _remote_mcp_call(
            url,
            "tools/call",
            {"name": real_tool_name, "arguments": arguments if isinstance(arguments, dict) else {}},
            timeout_s=30.0,
        )
    if invoker_type in {"npm_stdio", "package_stdio"}:
        command = invoker.get("command")
        cmd = [str(x) for x in command] if isinstance(command, list) else []
        if not cmd:
            raise ValueError("missing_package_command")
        env_override = invoker.get("env") if isinstance(invoker.get("env"), dict) else {}
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cccc-capability-runtime", "version": "1.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": real_tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                },
            },
        ]
        call_timeout_s = float(max(5, min(_env_int("CCCC_CAPABILITY_PACKAGE_CALL_TIMEOUT_SECONDS", 45), 180)))
        if env_override:
            responses = _stdio_mcp_roundtrip(
                cmd,
                requests,
                timeout_s=call_timeout_s,
                env_override=env_override,
            )
        else:
            responses = _stdio_mcp_roundtrip(cmd, requests, timeout_s=call_timeout_s)
        return _extract_jsonrpc_result(responses, req_id=2, operation="tools/call")
    raise ValueError(f"unsupported_invoker:{invoker_type or 'unknown'}")


def _invoke_installed_external_tool_with_aliases(
    install: Dict[str, Any],
    *,
    requested_tool_name: str,
    arguments: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    names = _tool_name_aliases(requested_tool_name)
    if not names:
        token = str(requested_tool_name or "").strip()
        if not token:
            raise ValueError("missing_tool_name")
        names = [token]
    last_unknown_error: Optional[Exception] = None
    for name in names:
        try:
            return (
                _invoke_installed_external_tool(
                    install,
                    real_tool_name=name,
                    arguments=arguments,
                ),
                name,
            )
        except Exception as e:
            if _is_unknown_tool_error_message(str(e)):
                last_unknown_error = e
                continue
            raise
    if isinstance(last_unknown_error, Exception):
        raise last_unknown_error
    raise RuntimeError(f"unknown tool: {requested_tool_name}")


def _sync_mcp_registry_source(catalog: Dict[str, Any], *, force: bool = False) -> int:
    sources = catalog["sources"]
    state = sources["mcp_registry_official"]
    interval_s = max(60, _env_int("CCCC_CAPABILITY_MCP_SYNC_INTERVAL_SECONDS", 6 * 3600))
    stale = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
    if (not force) and stale is not None and stale < interval_s:
        return 0

    max_pages = max(1, min(_env_int("CCCC_CAPABILITY_MCP_MAX_PAGES", 5), 50))
    records = catalog["records"]
    updated_since = str(state.get("updated_since") or "").strip()
    cursor = str(state.get("next_cursor") or "").strip()
    page = 0
    upserted = 0
    now_iso = utc_now_iso()

    try:
        while page < max_pages:
            params: Dict[str, str] = {"limit": str(_MCP_REGISTRY_PAGE_LIMIT)}
            if updated_since:
                params["updated_since"] = updated_since
            if cursor:
                params["cursor"] = cursor
            url = f"{_MCP_REGISTRY_BASE}/v0.1/servers?{urlencode(params)}"
            data = _http_get_json_obj(url, timeout=12.0)

            servers = data.get("servers")
            if not isinstance(servers, list):
                servers = []
            for item in servers:
                record = _normalize_mcp_registry_record(item, synced_at=now_iso)
                if record is None:
                    continue
                records[str(record["capability_id"])] = record
                upserted += 1

            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            next_cursor = str(metadata.get("nextCursor") or "").strip()
            cursor = next_cursor
            page += 1
            if not cursor:
                break

        state["last_synced_at"] = now_iso
        state["staleness_seconds"] = 0
        state["sync_state"] = "fresh" if not cursor else "stale"
        state["error"] = ""
        state["record_count"] = sum(
            1
            for item in records.values()
            if isinstance(item, dict) and str(item.get("source_id") or "") == "mcp_registry_official"
        )
        state["next_cursor"] = cursor
        if not cursor:
            state["updated_since"] = now_iso
        return upserted
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        state["sync_state"] = "degraded"
        state["error"] = str(e)
        state["staleness_seconds"] = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
        return 0


def _mcp_registry_search_servers(*, query: str, limit: int) -> List[Dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []
    lim = max(1, min(int(limit or 20), _MCP_REGISTRY_PAGE_LIMIT))
    params: Dict[str, str] = {
        "limit": str(lim),
        "search": q,
        "version": "latest",
    }
    url = f"{_MCP_REGISTRY_BASE}/v0.1/servers?{urlencode(params)}"
    data = _http_get_json_obj(url, timeout=8.0)
    servers = data.get("servers")
    if not isinstance(servers, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in servers:
        if isinstance(item, dict):
            out.append(item)
    return out


def _remote_search_mcp_registry_records(*, query: str, limit: int) -> List[Dict[str, Any]]:
    now_iso = utc_now_iso()
    rows = _mcp_registry_search_servers(query=query, limit=limit)
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        rec = _normalize_mcp_registry_record(item, synced_at=now_iso)
        if rec is None:
            continue
        cap_id = str(rec.get("capability_id") or "").strip()
        if not cap_id or cap_id in seen:
            continue
        seen.add(cap_id)
        out.append(rec)
    return out


_SKILLSMP_SKILL_URL_RE = re.compile(r"https?://skillsmp\.com/skills/[^\s)\]]+")
_SKILLSMP_DATE_RE = re.compile(r"\s+\d{4}-\d{2}-\d{2}\s*$")


def _js_literal_to_text(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        parsed = ast.literal_eval(s)
        return str(parsed or "")
    except Exception:
        return s.strip("'\"")


def _skillsmp_proxy_search_url(query: str) -> str:
    base = str(os.environ.get("CCCC_CAPABILITY_SKILLSMP_PROXY_BASE") or "").strip()
    if not base:
        base = "https://r.jina.ai/http://skillsmp.com/search"
    token = quote(str(query or "").strip())
    if "{query}" in base:
        return base.replace("{query}", token)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}q={token}"


def _parse_skillsmp_proxy_search_markdown(markdown: str, *, limit: int) -> List[Dict[str, Any]]:
    text = str(markdown or "")
    if not text.strip():
        return []
    rows: List[Dict[str, Any]] = []
    seen_uri: set[str] = set()
    now_iso = utc_now_iso()
    for m in _SKILLSMP_SKILL_URL_RE.finditer(text):
        source_uri = str(m.group(0) or "").strip().rstrip(").,")
        if not source_uri:
            continue
        if source_uri in seen_uri:
            continue
        seen_uri.add(source_uri)
        source_record_id = source_uri
        rec_hash = hashlib.sha1(source_record_id.encode("utf-8")).hexdigest()[:8]
        slug = source_uri.rstrip("/").split("/")[-1]
        slug_token = _sanitize_skill_id_token(slug, default="skill")
        cap_id = f"skill:skillsmp:{slug_token}-{rec_hash}"
        context = text[max(0, m.start() - 1200) : m.start()]
        one_line = " ".join((context.splitlines()[-1] if context.splitlines() else context).split())
        export_match = re.search(r"###\s*export\s+([A-Za-z0-9._-]+)", context)
        skill_name = ""
        if export_match:
            skill_name = _sanitize_skill_id_token(str(export_match.group(1) or ""), default="skill")
        if not skill_name:
            slug_parts = [p for p in slug_token.split("-") if p]
            if len(slug_parts) >= 2:
                skill_name = _sanitize_skill_id_token("-".join(slug_parts[-2:]), default="skill")
            else:
                skill_name = _sanitize_skill_id_token(slug_token, default="skill")
        repo_match = re.search(r'from\s+"([^"]+)"', context)
        description = one_line
        if repo_match:
            description = context[repo_match.end() :].strip()
        description = re.sub(r"^.*###\s*export\s+[A-Za-z0-9._-]+\s*", "", description).strip()
        description = _SKILLSMP_DATE_RE.sub("", description).strip()
        description = " ".join(description.split())
        if not description:
            description = f"SkillsMP skill candidate ({skill_name})"
        rows.append(
            {
                "capability_id": cap_id,
                "kind": "skill",
                "name": skill_name,
                "description_short": description[:600],
                "tags": ["skill", "external", "skillsmp", "remote_search"],
                "source_id": "skillsmp_remote",
                "source_tier": "tier2",
                "source_uri": source_uri,
                "source_record_id": source_record_id,
                "source_record_version": "",
                "updated_at_source": now_iso,
                "last_synced_at": now_iso,
                "sync_state": "remote",
                "install_mode": "builtin",
                "install_spec": {},
                "requirements": {},
                "license": "",
                "trust_tier": "tier2",
                "qualification_status": _QUAL_QUALIFIED,
                "qualification_reasons": [],
                "health_status": "remote",
                "enable_supported": True,
                "capsule_text": f"Skill: {skill_name}\nSummary: {description[:1000]}\nSource: {source_uri}",
                "requires_capabilities": [],
            }
        )
        if len(rows) >= max(1, int(limit or 20)):
            break
    return rows


def _skillsmp_api_search_url(query: str, *, page: int, limit: int) -> str:
    base = str(os.environ.get("CCCC_CAPABILITY_SKILLSMP_API_BASE") or "").strip()
    if not base:
        base = "https://skillsmp.com/api/v1/skills/search"
    params = {
        "q": str(query or "").strip(),
        "page": str(max(1, int(page or 1))),
        "limit": str(max(1, min(int(limit or 20), 100))),
        "sortBy": "stars",
    }
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


def _parse_skillsmp_api_payload(data: Dict[str, Any], *, limit: int) -> List[Dict[str, Any]]:
    now_iso = utc_now_iso()
    rows: List[Dict[str, Any]] = []
    candidates: List[Any] = []
    for key in ("items", "skills", "results", "data"):
        value = data.get(key)
        if isinstance(value, list):
            candidates = value
            break
        if isinstance(value, dict):
            for nested_key in ("items", "skills", "results"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    candidates = nested
                    break
            if candidates:
                break

    for item in candidates:
        if not isinstance(item, dict):
            continue
        slug = _sanitize_skill_id_token(str(item.get("slug") or item.get("id") or ""), default="")
        name = _sanitize_skill_id_token(str(item.get("name") or item.get("displayName") or slug), default="skill")
        if not slug:
            continue
        summary = str(
            item.get("summary")
            or item.get("description")
            or item.get("desc")
            or ""
        ).strip()
        if not summary:
            summary = f"SkillsMP skill candidate ({name})"
        source_uri = f"https://skillsmp.com/skills/{slug}"
        source_record_id = source_uri
        rec_hash = hashlib.sha1(source_record_id.encode("utf-8")).hexdigest()[:8]
        rows.append(
            {
                "capability_id": f"skill:skillsmp:{slug}-{rec_hash}",
                "kind": "skill",
                "name": name,
                "description_short": summary[:600],
                "tags": ["skill", "external", "skillsmp", "remote_search"],
                "source_id": "skillsmp_remote",
                "source_tier": "tier2",
                "source_uri": source_uri,
                "source_record_id": source_record_id,
                "source_record_version": str(item.get("version") or ""),
                "updated_at_source": now_iso,
                "last_synced_at": now_iso,
                "sync_state": "remote",
                "install_mode": "builtin",
                "install_spec": {},
                "requirements": {},
                "license": "",
                "trust_tier": "tier2",
                "qualification_status": _QUAL_QUALIFIED,
                "qualification_reasons": [],
                "health_status": "remote",
                "enable_supported": True,
                "capsule_text": f"Skill: {name}\nSummary: {summary[:1000]}\nSource: {source_uri}",
                "requires_capabilities": [],
            }
        )
        if len(rows) >= max(1, int(limit or 20)):
            break
    return rows


def _remote_search_skillsmp_records(*, query: str, limit: int) -> List[Dict[str, Any]]:
    if not _env_bool("CCCC_CAPABILITY_SOURCE_SKILLSMP_REMOTE_ENABLED", True):
        return []
    q = str(query or "").strip()
    if not q:
        return []
    timeout_s = float(max(3, min(_env_int("CCCC_CAPABILITY_SKILLSMP_REMOTE_TIMEOUT_SECONDS", 10), 25)))
    api_key = str(os.environ.get("CCCC_CAPABILITY_SKILLSMP_API_KEY") or "").strip()
    api_error = ""
    if api_key:
        try:
            api_url = _skillsmp_api_search_url(q, page=1, limit=limit)
            api_data = _http_get_json_obj(
                api_url,
                headers={"Authorization": f"Bearer {api_key}", "User-Agent": "cccc-capability-sync/1.0"},
                timeout=timeout_s,
            )
            api_rows = _parse_skillsmp_api_payload(api_data, limit=limit)
            if api_rows:
                return api_rows
            api_error = "skillsmp_api_empty"
        except HTTPError as e:
            if int(getattr(e, "code", 0) or 0) == 401:
                api_error = "skillsmp_api_auth_failed"
            else:
                api_error = f"skillsmp_api_http_{int(getattr(e, 'code', 0) or 0)}"
        except Exception as e:
            api_error = f"skillsmp_api_failed:{e}"

    url = _skillsmp_proxy_search_url(q)
    text = _http_get_text(url, headers={"User-Agent": "cccc-capability-sync/1.0"}, timeout=timeout_s)
    rows = _parse_skillsmp_proxy_search_markdown(text, limit=limit)
    if rows:
        return rows
    lowered = text.lower()
    if "missing_api_key" in lowered or "authorization header is required" in lowered:
        raise RuntimeError("skillsmp_api_key_required")
    if "cloudflare" in lowered and "blocked" in lowered:
        raise RuntimeError("skillsmp_blocked_by_cloudflare")
    if "loading skills" in lowered:
        if api_error:
            raise RuntimeError(f"{api_error};skillsmp_loading_only")
        raise RuntimeError("skillsmp_loading_only")
    if api_error:
        raise RuntimeError(f"{api_error};skillsmp_empty_or_unparsable")
    raise RuntimeError("skillsmp_empty_or_unparsable")


def _clawhub_api_url(*, query: str, limit: int, cursor: str = "") -> str:
    base = str(os.environ.get("CCCC_CAPABILITY_CLAWHUB_API_BASE") or "").strip()
    if not base:
        base = "https://clawhub.ai/api/v1/skills"
    params: Dict[str, str] = {
        "limit": str(max(1, min(int(limit or 20), 100))),
    }
    q = str(query or "").strip()
    if q:
        params["q"] = q
    cur = str(cursor or "").strip()
    if cur:
        params["cursor"] = cur
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


def _clawhub_item_to_record(item: Dict[str, Any], *, now_iso: str) -> Optional[Dict[str, Any]]:
    slug = _sanitize_skill_id_token(str(item.get("slug") or ""), default="")
    if not slug:
        return None
    source_uri = f"https://clawhub.ai/skills/{slug}"
    source_record_id = source_uri
    rec_hash = hashlib.sha1(source_record_id.encode("utf-8")).hexdigest()[:8]
    name = _sanitize_skill_id_token(str(item.get("displayName") or slug), default="skill")
    description = str(item.get("summary") or "").strip()
    if not description:
        description = f"ClawHub skill candidate ({name})"
    tags: List[str] = ["skill", "external", "clawhub", "remote_search"]
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    if int(stats.get("stars") or 0) > 0:
        tags.append("starred")
    version = ""
    latest_version = item.get("latestVersion")
    if isinstance(latest_version, dict):
        version = str(latest_version.get("version") or "").strip()
    else:
        tags_obj = item.get("tags") if isinstance(item.get("tags"), dict) else {}
        version = str(tags_obj.get("latest") or "").strip()

    return {
        "capability_id": f"skill:clawhub:{slug}-{rec_hash}",
        "kind": "skill",
        "name": name,
        "description_short": description[:600],
        "tags": tags,
        "source_id": "clawhub_remote",
        "source_tier": "tier2",
        "source_uri": source_uri,
        "source_record_id": source_record_id,
        "source_record_version": version,
        "updated_at_source": now_iso,
        "last_synced_at": now_iso,
        "sync_state": "remote",
        "install_mode": "builtin",
        "install_spec": {},
        "requirements": {},
        "license": "",
        "trust_tier": "tier2",
        "qualification_status": _QUAL_QUALIFIED,
        "qualification_reasons": [],
        "health_status": "remote",
        "enable_supported": True,
        "capsule_text": f"Skill: {name}\nSummary: {description[:1000]}\nSource: {source_uri}",
        "requires_capabilities": [],
    }


def _query_tokens_match(tokens: List[str], text: str) -> bool:
    if not tokens:
        return True
    hay = str(text or "").lower()
    return all(tok in hay for tok in tokens)


def _remote_search_clawhub_records(*, query: str, limit: int) -> List[Dict[str, Any]]:
    if not _env_bool("CCCC_CAPABILITY_SOURCE_CLAWHUB_REMOTE_ENABLED", True):
        return []
    q = str(query or "").strip()
    if not q:
        return []
    query_tokens = _tokenize_search_text(q)
    timeout_s = float(max(3, min(_env_int("CCCC_CAPABILITY_CLAWHUB_REMOTE_TIMEOUT_SECONDS", 10), 25)))
    max_pages = max(1, min(_env_int("CCCC_CAPABILITY_CLAWHUB_REMOTE_MAX_PAGES", 5), 15))
    page_size = max(1, min(_env_int("CCCC_CAPABILITY_CLAWHUB_REMOTE_PAGE_SIZE", 50), 100))
    requested = max(1, min(int(limit or 20), 100))
    rows: List[Dict[str, Any]] = []
    seen_caps: set[str] = set()
    now_iso = utc_now_iso()
    cursor = ""
    page = 0
    saw_payload = False
    while len(rows) < requested and page < max_pages:
        url = _clawhub_api_url(query=q, limit=page_size, cursor=cursor)
        data = _http_get_json_obj(url, timeout=timeout_s)
        items = data.get("items")
        if not isinstance(items, list):
            break
        saw_payload = True
        page += 1
        for item in items:
            if not isinstance(item, dict):
                continue
            rec = _clawhub_item_to_record(item, now_iso=now_iso)
            if not isinstance(rec, dict):
                continue
            search_text = " ".join(
                [
                    str(rec.get("name") or ""),
                    str(rec.get("description_short") or ""),
                    str(item.get("slug") or ""),
                    str(item.get("displayName") or ""),
                ]
            )
            if not _query_tokens_match(query_tokens, search_text):
                continue
            cap_id = str(rec.get("capability_id") or "")
            if not cap_id or cap_id in seen_caps:
                continue
            seen_caps.add(cap_id)
            rows.append(rec)
            if len(rows) >= requested:
                break
        cursor = str(data.get("nextCursor") or "").strip()
        if not cursor:
            break
    if rows:
        return rows[:requested]
    if saw_payload:
        return []
    raise RuntimeError("clawhub_empty_or_unparsable")


def _openclaw_tree_paths() -> List[str]:
    ttl_seconds = max(60, min(_env_int("CCCC_CAPABILITY_OPENCLAW_TREE_CACHE_TTL_SECONDS", 3600), 86_400))
    now = time.time()
    with _REMOTE_SOURCE_CACHE_LOCK:
        cache_paths = _OPENCLAW_TREE_CACHE.get("paths")
        fetched_at = float(_OPENCLAW_TREE_CACHE.get("fetched_at") or 0.0)
        if isinstance(cache_paths, list) and cache_paths and (now - fetched_at) < ttl_seconds:
            return [str(x) for x in cache_paths if str(x).strip()]

    data = _http_get_json_obj(_OPENCLAW_SKILLS_TREE_API, headers=_github_headers(), timeout=12.0)
    tree = data.get("tree")
    if not isinstance(tree, list):
        return []
    paths: List[str] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "blob":
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if not path.lower().endswith("skill.md"):
            continue
        paths.append(path)
    paths = sorted(set(paths))
    with _REMOTE_SOURCE_CACHE_LOCK:
        _OPENCLAW_TREE_CACHE["fetched_at"] = now
        _OPENCLAW_TREE_CACHE["paths"] = list(paths)
    return paths


def _openclaw_frontmatter_for_path(path: str) -> Tuple[Dict[str, Any], str]:
    safe_path = "/".join(part for part in str(path or "").split("/") if part and part not in {".", ".."})
    if not safe_path:
        return {}, ""
    url = f"{_OPENCLAW_SKILLS_BLOB_BASE}/{quote(safe_path, safe='/')}"
    text = _http_get_text(url, headers=_github_headers(), timeout=8.0)
    try:
        frontmatter, body = _split_frontmatter(text)
        return frontmatter, body
    except Exception:
        return {}, text


def _remote_search_openclaw_skill_records(*, query: str, limit: int) -> List[Dict[str, Any]]:
    if not _env_bool("CCCC_CAPABILITY_SOURCE_OPENCLAW_SKILLS_REMOTE_ENABLED", True):
        return []
    q = str(query or "").strip()
    if not q:
        return []
    query_tokens = _tokenize_search_text(q)
    requested = max(1, min(int(limit or 20), 100))
    paths = _openclaw_tree_paths()
    if not paths:
        return []

    candidates: List[str] = []
    for path in paths:
        hay = path.lower()
        if _query_tokens_match(query_tokens, hay):
            candidates.append(path)
    if not candidates:
        return []

    fetch_frontmatter_max = max(
        0,
        min(_env_int("CCCC_CAPABILITY_OPENCLAW_FRONTMATTER_FETCH_MAX", 10), 40),
    )
    now_iso = utc_now_iso()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, path in enumerate(candidates[: max(requested * 3, requested)]):
        source_record_id = f"openclaw/skills:{path}"
        rec_hash = hashlib.sha1(source_record_id.encode("utf-8")).hexdigest()[:8]
        tokens = [p for p in path.split("/") if p]
        slug_hint = tokens[-2] if len(tokens) >= 2 else tokens[-1]
        skill_name = _sanitize_skill_id_token(slug_hint, default="skill")
        cap_id = f"skill:openclaw:{skill_name}-{rec_hash}"
        if cap_id in seen:
            continue
        seen.add(cap_id)
        source_uri = f"https://github.com/openclaw/skills/blob/main/{quote(path, safe='/')}"
        frontmatter: Dict[str, Any] = {}
        body = ""
        if idx < fetch_frontmatter_max:
            try:
                frontmatter, body = _openclaw_frontmatter_for_path(path)
            except Exception:
                frontmatter = {}
                body = ""
        if frontmatter:
            maybe_name = _sanitize_skill_id_token(str(frontmatter.get("name") or ""), default=skill_name)
            if maybe_name:
                skill_name = maybe_name
            description = str(frontmatter.get("description") or "").strip()
            requires_capabilities = _extract_skill_dependencies(frontmatter)
            capsule_text = _extract_skill_capsule(frontmatter, body)
            license_text = str(frontmatter.get("license") or "").strip()
        else:
            description = ""
            requires_capabilities = []
            capsule_text = ""
            license_text = ""

        if not description:
            description = f"OpenClaw skill candidate ({skill_name}) from {path}"
        if not capsule_text:
            capsule_text = f"Skill: {skill_name}\nSummary: {description}\nSource: {source_uri}"
        out.append(
            {
                "capability_id": cap_id,
                "kind": "skill",
                "name": skill_name,
                "description_short": description[:600],
                "tags": ["skill", "external", "openclaw", "remote_search"],
                "source_id": "openclaw_skills_remote",
                "source_tier": "tier2",
                "source_uri": source_uri,
                "source_record_id": source_record_id,
                "source_record_version": "",
                "updated_at_source": now_iso,
                "last_synced_at": now_iso,
                "sync_state": "remote",
                "install_mode": "builtin",
                "install_spec": {},
                "requirements": {},
                "license": license_text,
                "trust_tier": "tier2",
                "qualification_status": _QUAL_QUALIFIED,
                "qualification_reasons": [],
                "health_status": "remote",
                "enable_supported": True,
                "capsule_text": capsule_text[:2400],
                "requires_capabilities": requires_capabilities[:32],
            }
        )
        if len(out) >= requested:
            break
    return out


def _parse_clawskills_data_js(*, script: str, query: str, limit: int) -> List[Dict[str, Any]]:
    text = str(script or "")
    if not text.strip():
        return []
    start = text.find("var SKILLS_DATA")
    if start < 0:
        return []
    list_start = text.find("[", start)
    list_end = text.rfind("]")
    if list_start < 0 or list_end < 0 or list_end <= list_start:
        return []
    payload = text[list_start : list_end + 1]
    tokens = _tokenize_search_text(query)
    requested = max(1, min(int(limit or 20), 100))
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    now_iso = utc_now_iso()
    for obj_match in _CLAWSKILLS_ENTRY_RE.finditer(payload):
        obj_text = str(obj_match.group(0) or "").strip()
        if not obj_text:
            continue
        fields: Dict[str, str] = {}
        for key_match in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*('(?:\\\\'|[^'])*'|\"(?:\\\\\"|[^\"])*\")", obj_text):
            key = str(key_match.group(1) or "").strip().lower()
            value = _js_literal_to_text(key_match.group(2))
            if key:
                fields[key] = value
        slug = _sanitize_skill_id_token(fields.get("slug"), default="")
        if not slug:
            continue
        name = _sanitize_skill_id_token(fields.get("name"), default=slug)
        desc = str(fields.get("desc") or fields.get("description") or "").strip()
        category = str(fields.get("category") or "").strip()
        author = _sanitize_skill_id_token(fields.get("author"), default="")
        hay = " ".join([slug, name, desc, category, author]).lower()
        if not _query_tokens_match(tokens, hay):
            continue
        source_uri = f"https://clawskills.co/#skill-{slug}"
        source_record_id = f"clawskills:{slug}"
        rec_hash = hashlib.sha1(source_record_id.encode("utf-8")).hexdigest()[:8]
        cap_id = f"skill:clawskills:{slug}-{rec_hash}"
        if cap_id in seen:
            continue
        seen.add(cap_id)
        if not desc:
            desc = f"clawskills.co skill candidate ({name})"
        tags = ["skill", "external", "clawskills", "remote_search"]
        if category:
            tags.append(_sanitize_skill_id_token(category, default="category"))
        if author:
            tags.append(f"author:{author}")
        rows.append(
            {
                "capability_id": cap_id,
                "kind": "skill",
                "name": name,
                "description_short": desc[:600],
                "tags": tags,
                "source_id": "clawskills_remote",
                "source_tier": "tier2",
                "source_uri": source_uri,
                "source_record_id": source_record_id,
                "source_record_version": "",
                "updated_at_source": now_iso,
                "last_synced_at": now_iso,
                "sync_state": "remote",
                "install_mode": "builtin",
                "install_spec": {},
                "requirements": {},
                "license": "",
                "trust_tier": "tier2",
                "qualification_status": _QUAL_QUALIFIED,
                "qualification_reasons": [],
                "health_status": "remote",
                "enable_supported": True,
                "capsule_text": f"Skill: {name}\nSummary: {desc[:1000]}\nSource: {source_uri}",
                "requires_capabilities": [],
            }
        )
        if len(rows) >= requested:
            break
    return rows


def _remote_search_clawskills_records(*, query: str, limit: int) -> List[Dict[str, Any]]:
    if not _env_bool("CCCC_CAPABILITY_SOURCE_CLAWSKILLS_REMOTE_ENABLED", True):
        return []
    q = str(query or "").strip()
    if not q:
        return []
    url = str(os.environ.get("CCCC_CAPABILITY_CLAWSKILLS_DATA_URL") or "").strip() or _CLAWSKILLS_DATA_URL_DEFAULT
    timeout_s = float(max(3, min(_env_int("CCCC_CAPABILITY_CLAWSKILLS_REMOTE_TIMEOUT_SECONDS", 12), 30)))
    text = _http_get_text(url, headers={"User-Agent": "cccc-capability-sync/1.0"}, timeout=timeout_s)
    rows = _parse_clawskills_data_js(script=text, query=q, limit=limit)
    if rows:
        return rows
    if "var SKILLS_DATA" in text:
        return []
    raise RuntimeError("clawskills_empty_or_unparsable")


def _remote_search_skill_records(*, query: str, limit: int, source_filter: str = "") -> List[Dict[str, Any]]:
    requested = max(1, min(int(limit or 20), 100))
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    errors: List[str] = []
    source_hint = str(source_filter or "").strip().lower()

    def _append_rows(rows: List[Dict[str, Any]]) -> None:
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            cap_id = str(rec.get("capability_id") or "").strip()
            if not cap_id or cap_id in seen:
                continue
            seen.add(cap_id)
            out.append(rec)

    adapters = [
        (
            "skillsmp",
            "skillsmp_remote",
            _remote_search_skillsmp_records,
            max(1, min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_SKILLSMP_LIMIT", requested), 100)),
        ),
        (
            "clawhub",
            "clawhub_remote",
            _remote_search_clawhub_records,
            max(1, min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_CLAWHUB_LIMIT", requested), 100)),
        ),
        (
            "openclaw",
            "openclaw_skills_remote",
            _remote_search_openclaw_skill_records,
            max(1, min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_OPENCLAW_LIMIT", requested), 100)),
        ),
        (
            "clawskills",
            "clawskills_remote",
            _remote_search_clawskills_records,
            max(1, min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_CLAWSKILLS_LIMIT", requested), 100)),
        ),
    ]
    if source_hint in {
        "skillsmp_remote",
        "clawhub_remote",
        "openclaw_skills_remote",
        "clawskills_remote",
    }:
        adapters = [item for item in adapters if str(item[1] or "").strip().lower() == source_hint]
    for source_name, _source_id, fn, source_limit in adapters:
        if len(out) >= requested:
            break
        needed = max(1, requested - len(out))
        try:
            rows = fn(query=query, limit=min(needed, source_limit))
            _append_rows(rows if isinstance(rows, list) else [])
        except Exception as e:
            errors.append(f"{source_name}:{e}")
            continue

    if out:
        return out[:requested]
    if errors:
        raise RuntimeError("; ".join(errors))
    return []


def _fetch_mcp_registry_record_by_server_name(server_name: str) -> Optional[Dict[str, Any]]:
    name = str(server_name or "").strip()
    if not name:
        return None
    rows = _remote_search_mcp_registry_records(query=name, limit=20)
    for rec in rows:
        cap_id = str(rec.get("capability_id") or "").strip()
        if cap_id == f"mcp:{name}":
            return rec
    return None


def _normalize_mcp_registry_record(raw: Any, *, synced_at: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    server = raw.get("server") if isinstance(raw.get("server"), dict) else raw
    if not isinstance(server, dict):
        return None
    name = str(server.get("name") or "").strip()
    if not name:
        return None

    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    official = meta.get("io.modelcontextprotocol.registry/official")
    official = official if isinstance(official, dict) else {}
    status = str(official.get("status") or "active").strip().lower()
    updated_at_source = str(official.get("updatedAt") or official.get("publishedAt") or "").strip()
    if not updated_at_source:
        updated_at_source = synced_at

    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
    install_mode = "unknown"
    install_spec: Dict[str, Any] = {}
    if packages:
        install_mode = "package"
        pkg = packages[0] if isinstance(packages[0], dict) else {}
        runtime_arguments = _normalize_registry_argument_entries(pkg.get("runtimeArguments"))
        package_arguments = _normalize_registry_argument_entries(pkg.get("packageArguments"))
        required_env = _normalize_registry_env_names(pkg.get("environmentVariables"), required_only=True)
        required_env_from_runtime = _extract_required_env_from_runtime_arguments(pkg.get("runtimeArguments"))
        for env_name in required_env_from_runtime:
            if env_name not in required_env:
                required_env.append(env_name)
        env_names = _normalize_registry_env_names(pkg.get("environmentVariables"), required_only=False)
        raw_registry_type = str(pkg.get("registryType") or "").strip()
        install_spec = {
            "registry_type": _normalize_registry_type_token(raw_registry_type),
            "registry_type_raw": raw_registry_type,
            "identifier": str(pkg.get("identifier") or "").strip(),
            "version": str(pkg.get("version") or server.get("version") or "").strip(),
            "runtime_hint": str(pkg.get("runtimeHint") or "").strip(),
            "transport": str((pkg.get("transport") or {}).get("type") or "").strip()
            if isinstance(pkg.get("transport"), dict)
            else "",
            "runtime_arguments": runtime_arguments,
            "package_arguments": package_arguments,
            "required_env": required_env,
            "env_names": env_names,
        }
    elif remotes:
        install_mode = "remote_only"
        remote = remotes[0] if isinstance(remotes[0], dict) else {}
        install_spec = {
            "transport": str(remote.get("type") or "").strip(),
            "url": str(remote.get("url") or "").strip(),
        }

    supported, _ = _supported_external_install_record(
        {
            "install_mode": install_mode,
            "install_spec": install_spec,
        }
    )
    qualification = _QUAL_UNAVAILABLE
    if status == "deleted":
        qualification = _QUAL_BLOCKED
    elif supported:
        qualification = _QUAL_QUALIFIED
    else:
        qualification = _QUAL_UNAVAILABLE

    source_uri = ""
    repository = server.get("repository")
    if isinstance(repository, dict):
        source_uri = str(repository.get("url") or "").strip()
    if not source_uri:
        source_uri = str(server.get("websiteUrl") or "").strip()
    if not source_uri:
        source_uri = f"{_MCP_REGISTRY_BASE}/v0.1/servers/{quote(name, safe='')}/versions/{quote(str(server.get('version') or 'latest'), safe='')}"

    return {
        "capability_id": f"mcp:{name}",
        "kind": "mcp_toolpack",
        "name": name,
        "description_short": str(server.get("description") or server.get("title") or "").strip(),
        "tags": ["mcp", "external", "registry"],
        "source_id": "mcp_registry_official",
        "source_tier": "tier1",
        "source_uri": source_uri,
        "source_record_id": name,
        "source_record_version": str(server.get("version") or "").strip(),
        "updated_at_source": updated_at_source,
        "last_synced_at": synced_at,
        "sync_state": "fresh",
        "install_mode": install_mode,
        "install_spec": install_spec,
        "requirements": {},
        "license": "",
        "trust_tier": "tier1",
        "qualification_status": qualification,
        "qualification_reasons": (
            ["registry_status_deleted"]
            if qualification == _QUAL_BLOCKED
            else (["external_install_supported"] if qualification == _QUAL_QUALIFIED else ["external_install_unavailable"])
        ),
        "health_status": "ok" if status == "active" else status,
        "enable_supported": bool(supported and status == "active"),
    }


def _split_frontmatter(markdown: str) -> Tuple[Dict[str, Any], str]:
    raw = str(markdown or "")
    if not raw.startswith("---"):
        raise ValueError("missing YAML frontmatter")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError("frontmatter not closed")
    doc = yaml.safe_load(parts[1])
    if not isinstance(doc, dict):
        raise ValueError("frontmatter must be a mapping")
    body = str(parts[2] or "")
    return doc, body


def _parse_frontmatter(markdown: str) -> Dict[str, Any]:
    doc, _ = _split_frontmatter(markdown)
    return doc


def _extract_skill_capsule(frontmatter: Dict[str, Any], body: str) -> str:
    """Build a short deterministic skill capsule for runtime use."""
    lines: List[str] = []
    name = str(frontmatter.get("name") or "").strip()
    desc = str(frontmatter.get("description") or "").strip()
    if name:
        lines.append(f"Skill: {name}")
    if desc:
        lines.append(f"Summary: {desc}")
    raw_body = str(body or "").strip()
    if raw_body:
        snippet = raw_body[:1600].strip()
        if snippet:
            lines.append("")
            lines.append("Notes:")
            lines.append(snippet)
    out = "\n".join(lines).strip()
    return out[:2400]


def _extract_skill_dependencies(frontmatter: Dict[str, Any]) -> List[str]:
    """Extract deterministic capability dependency list from frontmatter."""
    out: List[str] = []
    for key in ("requires_capabilities", "capabilities", "requires"):
        raw = frontmatter.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            cid = str(item or "").strip()
            if cid and cid not in out:
                out.append(cid)
    return out[:32]


def _validate_agentskill_frontmatter(frontmatter: Dict[str, Any], *, dir_name: str) -> List[str]:
    errors: List[str] = []
    name = str(frontmatter.get("name") or "").strip()
    desc = str(frontmatter.get("description") or "").strip()
    if not name:
        errors.append("missing name")
    else:
        if len(name) > 64:
            errors.append("name too long")
        if not _SKILL_NAME_RE.fullmatch(name):
            errors.append("invalid name format")
        if str(dir_name or "").strip() and name != str(dir_name or "").strip():
            errors.append("name does not match directory")
    if not desc:
        errors.append("missing description")
    elif len(desc) > 1024:
        errors.append("description too long")
    return errors


def _sync_anthropic_skills_source(catalog: Dict[str, Any], *, force: bool = False) -> int:
    sources = catalog["sources"]
    state = sources["anthropic_skills"]
    interval_s = max(60, _env_int("CCCC_CAPABILITY_SKILL_SYNC_INTERVAL_SECONDS", 12 * 3600))
    stale = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
    if (not force) and stale is not None and stale < interval_s:
        return 0

    records = catalog["records"]
    now_iso = utc_now_iso()
    try:
        dirs_data = _http_get_json(
            f"{_GITHUB_API_BASE}/repos/anthropics/skills/contents/skills?per_page=200",
            headers=_github_headers(),
            timeout=12.0,
        )
        if not isinstance(dirs_data, list):
            raise ValueError("unexpected GitHub response shape")
        upserted = 0
        for item in dirs_data:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip() != "dir":
                continue
            dir_name = str(item.get("name") or "").strip()
            if not dir_name:
                continue
            raw_url = f"{_RAW_GITHUB_BASE}/anthropics/skills/main/skills/{dir_name}/SKILL.md"
            req = Request(raw_url, method="GET")
            for k, v in _github_headers().items():
                req.add_header(k, v)
            with urlopen(req, timeout=12.0) as resp:
                md = resp.read().decode("utf-8", errors="replace")
            frontmatter, body = _split_frontmatter(md)
            errors = _validate_agentskill_frontmatter(frontmatter, dir_name=dir_name)
            name = str(frontmatter.get("name") or dir_name).strip()
            description = str(frontmatter.get("description") or "").strip()
            license_text = str(frontmatter.get("license") or "").strip()
            tags_raw = frontmatter.get("tags")
            tags = (
                [str(x).strip() for x in tags_raw if str(x).strip()]
                if isinstance(tags_raw, list)
                else []
            )
            capsule_text = _extract_skill_capsule(frontmatter, body)
            requires_capabilities = _extract_skill_dependencies(frontmatter)
            qualification = "qualified"
            reasons: List[str] = []
            if errors:
                qualification = _QUAL_BLOCKED
                reasons.extend(errors)
            else:
                qualification = _QUAL_QUALIFIED

            record = {
                "capability_id": f"skill:anthropic:{name}",
                "kind": "skill",
                "name": name,
                "description_short": description,
                "tags": ["skill", "external", "anthropic", *tags],
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "source_uri": f"https://github.com/anthropics/skills/tree/main/skills/{dir_name}",
                "source_record_id": dir_name,
                "source_record_version": str(item.get("sha") or "").strip(),
                "updated_at_source": now_iso,
                "last_synced_at": now_iso,
                "sync_state": "fresh",
                "install_mode": "builtin",
                "install_spec": {},
                "requirements": {},
                "license": license_text,
                "trust_tier": "tier1",
                "qualification_status": qualification,
                "qualification_reasons": reasons,
                "health_status": "ok",
                "enable_supported": qualification != _QUAL_BLOCKED,
                "capsule_text": capsule_text,
                "requires_capabilities": requires_capabilities,
            }
            records[str(record["capability_id"])] = record
            upserted += 1

        state["last_synced_at"] = now_iso
        state["staleness_seconds"] = 0
        state["sync_state"] = "fresh"
        state["error"] = ""
        state["record_count"] = sum(
            1
            for item in records.values()
            if isinstance(item, dict) and str(item.get("source_id") or "") == "anthropic_skills"
        )
        return upserted
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        state["sync_state"] = "degraded"
        state["error"] = str(e)
        state["staleness_seconds"] = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
        return 0


def _mark_source_disabled(catalog: Dict[str, Any], source_id: str) -> None:
    sources = catalog.get("sources") if isinstance(catalog.get("sources"), dict) else {}
    state = sources.get(source_id) if isinstance(sources.get(source_id), dict) else _source_state_template("never")
    state["sync_state"] = "disabled"
    state["error"] = "source_disabled_by_policy"
    state["staleness_seconds"] = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
    sources[source_id] = state
    catalog["sources"] = sources


def _sync_tier_rank(tier: str) -> int:
    t = str(tier or "").strip().lower()
    if t in {"builtin", "official", "tier1"}:
        return 0
    if t in {"tier2"}:
        return 1
    return 2


def _qualification_rank(q: str) -> int:
    status = str(q or "").strip().lower()
    if status == _QUAL_QUALIFIED:
        return 0
    if status == _QUAL_UNAVAILABLE:
        return 1
    if status == _QUAL_BLOCKED:
        return 2
    return 3


def _catalog_record_sort_key(item: Dict[str, Any]) -> Tuple[int, int, int, int, str]:
    source_tier = str(item.get("source_tier") or "")
    qual = str(item.get("qualification_status") or "")
    updated_at_source = str(item.get("updated_at_source") or "")
    last_synced_at = str(item.get("last_synced_at") or "")
    name = str(item.get("name") or item.get("capability_id") or "").lower()
    updated_dt = parse_utc_iso(updated_at_source)
    synced_dt = parse_utc_iso(last_synced_at)
    updated_rank = -int(updated_dt.timestamp()) if updated_dt is not None else 0
    synced_rank = -int(synced_dt.timestamp()) if synced_dt is not None else 0
    return (
        _sync_tier_rank(source_tier),
        _qualification_rank(qual),
        updated_rank,
        synced_rank,
        name,
    )


def _catalog_max_records() -> int:
    return _quota_limit(
        "CCCC_CAPABILITY_CATALOG_MAX_RECORDS",
        20_000,
        minimum=200,
        maximum=500_000,
    )


def _prune_catalog_records(catalog: Dict[str, Any]) -> int:
    records = catalog.get("records") if isinstance(catalog.get("records"), dict) else {}
    if not isinstance(records, dict):
        return 0
    max_records = _catalog_max_records()
    if len(records) <= max_records:
        return 0
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for cap_id, rec in records.items():
        if not isinstance(rec, dict):
            continue
        rows.append((str(cap_id or ""), dict(rec)))
    rows.sort(key=lambda x: _catalog_record_sort_key(x[1]))
    # Keep best candidates, then stable-id for deterministic output.
    keep_rows = rows[:max_records]
    keep_rows.sort(key=lambda x: str(x[0] or ""))
    keep_ids = {str(cap_id or "") for cap_id, _ in keep_rows}
    pruned = max(0, len(records) - len(keep_ids))
    if pruned <= 0:
        return 0
    catalog["records"] = {cid: rec for cid, rec in records.items() if str(cid or "") in keep_ids}
    return pruned


def _refresh_source_record_counts(catalog: Dict[str, Any]) -> None:
    sources = catalog.get("sources") if isinstance(catalog.get("sources"), dict) else {}
    records = catalog.get("records") if isinstance(catalog.get("records"), dict) else {}
    counts = {source_id: 0 for source_id in _SOURCE_IDS}
    for rec in records.values() if isinstance(records, dict) else []:
        if not isinstance(rec, dict):
            continue
        sid = str(rec.get("source_id") or "").strip()
        if sid in counts:
            counts[sid] += 1
    for source_id in _SOURCE_IDS:
        state = sources.get(source_id) if isinstance(sources.get(source_id), dict) else _source_state_template("never")
        state["record_count"] = int(counts.get(source_id) or 0)
        sources[source_id] = state
    catalog["sources"] = sources


def _sync_catalog(catalog_doc: Dict[str, Any], *, force: bool) -> Dict[str, Any]:
    before = json.dumps(catalog_doc, ensure_ascii=False, sort_keys=True)
    upserted: Dict[str, int] = {}

    if _env_bool("CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED", True):
        upserted["mcp_registry_official"] = int(_sync_mcp_registry_source(catalog_doc, force=force))
    else:
        _mark_source_disabled(catalog_doc, "mcp_registry_official")
        upserted["mcp_registry_official"] = 0

    if _env_bool("CCCC_CAPABILITY_SOURCE_ANTHROPIC_SKILLS_ENABLED", True):
        upserted["anthropic_skills"] = int(_sync_anthropic_skills_source(catalog_doc, force=force))
    else:
        _mark_source_disabled(catalog_doc, "anthropic_skills")
        upserted["anthropic_skills"] = 0

    pruned = _prune_catalog_records(catalog_doc)
    _refresh_source_record_counts(catalog_doc)

    after = json.dumps(catalog_doc, ensure_ascii=False, sort_keys=True)
    return {
        "changed": (before != after),
        "upserted": upserted,
        "upserted_total": sum(max(0, int(v or 0)) for v in upserted.values()),
        "pruned": int(pruned),
    }


def _auto_sync_catalog(catalog_doc: Dict[str, Any]) -> bool:
    return bool(_sync_catalog(catalog_doc, force=False).get("changed"))


def sync_capability_catalog_once(*, force: bool = False) -> Dict[str, Any]:
    """Run one capability catalog sync pass and persist if changed.

    Intended for daemon-owned background sync loops. Does not raise.
    """
    try:
        with _CATALOG_LOCK:
            catalog_path, catalog_doc = _load_catalog_doc()
            result = _sync_catalog(catalog_doc, force=bool(force))
            if bool(result.get("changed")):
                _save_catalog_doc(catalog_path, catalog_doc)
        return {
            "ok": True,
            "changed": bool(result.get("changed")),
            "upserted_total": int(result.get("upserted_total") or 0),
            "upserted": result.get("upserted") if isinstance(result.get("upserted"), dict) else {},
            "pruned": int(result.get("pruned") or 0),
            "source_states": _render_source_states(catalog_doc),
        }
    except Exception as e:
        return {
            "ok": False,
            "changed": False,
            "upserted_total": 0,
            "upserted": {},
            "pruned": 0,
            "error": str(e),
            "source_states": {},
        }


def _collect_enabled_capabilities(state_doc: Dict[str, Any], *, group_id: str, actor_id: str) -> Tuple[List[str], bool]:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    enabled: List[str] = []
    mutated = False

    group_enabled = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
    enabled.extend(list(group_enabled.get(gid) or []))

    actor_enabled = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
    if isinstance(actor_enabled.get(gid), dict):
        enabled.extend(list((actor_enabled.get(gid) or {}).get(aid) or []))

    now = datetime.now(timezone.utc)
    session_enabled = state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
    group_sessions = session_enabled.get(gid) if isinstance(session_enabled.get(gid), dict) else {}
    entries = list(group_sessions.get(aid) or [])
    remaining: List[Dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            mutated = True
            continue
        cap_id = str(item.get("capability_id") or "").strip()
        expires_at = str(item.get("expires_at") or "").strip()
        dt = parse_utc_iso(expires_at)
        if not cap_id or dt is None:
            mutated = True
            continue
        if dt <= now:
            mutated = True
            continue
        enabled.append(cap_id)
        remaining.append({"capability_id": cap_id, "expires_at": expires_at})

    if remaining:
        session_enabled.setdefault(gid, {})
        session_enabled[gid][aid] = remaining
    else:
        if isinstance(session_enabled.get(gid), dict) and aid in session_enabled.get(gid, {}):
            mutated = True
            session_enabled[gid].pop(aid, None)
        if isinstance(session_enabled.get(gid), dict) and not session_enabled.get(gid):
            session_enabled.pop(gid, None)
    state_doc["session_enabled"] = session_enabled

    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: List[str] = []
    for cap_id in enabled:
        cid = str(cap_id or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)
    return ordered, mutated


def _set_enabled_capability(
    state_doc: Dict[str, Any],
    *,
    group_id: str,
    actor_id: str,
    scope: str,
    capability_id: str,
    enabled: bool,
    ttl_seconds: int,
) -> None:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    cap_id = str(capability_id or "").strip()
    if scope == "group":
        group_enabled = state_doc.setdefault("group_enabled", {})
        items = set(group_enabled.get(gid) or [])
        if enabled:
            items.add(cap_id)
        else:
            items.discard(cap_id)
        if items:
            group_enabled[gid] = sorted(items)
        else:
            group_enabled.pop(gid, None)
        return

    if scope == "actor":
        actor_enabled = state_doc.setdefault("actor_enabled", {})
        group_map = actor_enabled.setdefault(gid, {})
        items = set(group_map.get(aid) or [])
        if enabled:
            items.add(cap_id)
        else:
            items.discard(cap_id)
        if items:
            group_map[aid] = sorted(items)
        else:
            group_map.pop(aid, None)
        if not group_map:
            actor_enabled.pop(gid, None)
        return

    # session scope
    session_enabled = state_doc.setdefault("session_enabled", {})
    group_map = session_enabled.setdefault(gid, {})
    entries = [x for x in list(group_map.get(aid) or []) if isinstance(x, dict)]
    entries = [x for x in entries if str(x.get("capability_id") or "").strip() != cap_id]
    if enabled:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(60, min(ttl_seconds, 24 * 3600)))
        ).isoformat().replace("+00:00", "Z")
        entries.append({"capability_id": cap_id, "expires_at": expires_at})
    if entries:
        group_map[aid] = entries
    else:
        group_map.pop(aid, None)
    if not group_map:
        session_enabled.pop(gid, None)


def _remove_capability_bindings(
    state_doc: Dict[str, Any],
    *,
    group_id: str,
    capability_id: str,
) -> int:
    gid = str(group_id or "").strip()
    cap_id = str(capability_id or "").strip()
    if not gid or not cap_id:
        return 0
    removed = 0

    group_enabled = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
    if isinstance(group_enabled.get(gid), list):
        before = set(group_enabled.get(gid) or [])
        if cap_id in before:
            before.discard(cap_id)
            removed += 1
            if before:
                group_enabled[gid] = sorted(before)
            else:
                group_enabled.pop(gid, None)
        state_doc["group_enabled"] = group_enabled

    actor_enabled = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
    per_group_actor = actor_enabled.get(gid) if isinstance(actor_enabled.get(gid), dict) else {}
    actor_keys = list(per_group_actor.keys()) if isinstance(per_group_actor, dict) else []
    for aid in actor_keys:
        items = set(per_group_actor.get(aid) or [])
        if cap_id in items:
            items.discard(cap_id)
            removed += 1
            if items:
                per_group_actor[aid] = sorted(items)
            else:
                per_group_actor.pop(aid, None)
    if isinstance(actor_enabled, dict):
        if per_group_actor:
            actor_enabled[gid] = per_group_actor
        else:
            actor_enabled.pop(gid, None)
        state_doc["actor_enabled"] = actor_enabled

    session_enabled = state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
    per_group_session = session_enabled.get(gid) if isinstance(session_enabled.get(gid), dict) else {}
    session_keys = list(per_group_session.keys()) if isinstance(per_group_session, dict) else []
    for aid in session_keys:
        entries = per_group_session.get(aid) if isinstance(per_group_session.get(aid), list) else []
        keep: List[Dict[str, str]] = []
        hit = False
        for item in entries:
            if not isinstance(item, dict):
                continue
            if str(item.get("capability_id") or "").strip() == cap_id:
                hit = True
                continue
            keep.append(item)
        if hit:
            removed += 1
        if keep:
            per_group_session[aid] = keep
        else:
            per_group_session.pop(aid, None)
    if isinstance(session_enabled, dict):
        if per_group_session:
            session_enabled[gid] = per_group_session
        else:
            session_enabled.pop(gid, None)
        state_doc["session_enabled"] = session_enabled

    return removed


def _remove_capability_bindings_all_groups(
    state_doc: Dict[str, Any],
    *,
    capability_id: str,
) -> int:
    cap_id = str(capability_id or "").strip()
    if not cap_id:
        return 0
    group_ids: set[str] = set()
    group_enabled = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
    actor_enabled = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
    session_enabled = state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
    group_ids.update(str(gid).strip() for gid in group_enabled.keys() if str(gid).strip())
    group_ids.update(str(gid).strip() for gid in actor_enabled.keys() if str(gid).strip())
    group_ids.update(str(gid).strip() for gid in session_enabled.keys() if str(gid).strip())
    removed = 0
    for gid in sorted(group_ids):
        removed += _remove_capability_bindings(state_doc, group_id=gid, capability_id=cap_id)
    return removed


def _set_blocked_capability(
    state_doc: Dict[str, Any],
    *,
    scope: str,
    group_id: str,
    capability_id: str,
    by: str,
    reason: str,
    ttl_seconds: int,
) -> Dict[str, str]:
    cap_id = str(capability_id or "").strip()
    gid = str(group_id or "").strip()
    actor = str(by or "").strip()
    ttl = max(0, min(int(ttl_seconds or 0), 30 * 24 * 3600))
    now_iso = utc_now_iso()
    expires_at = ""
    if ttl > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
    entry = {
        "reason": str(reason or "").strip(),
        "by": actor,
        "blocked_at": now_iso,
        "expires_at": expires_at,
    }
    if scope == "global":
        global_blocked = state_doc.setdefault("global_blocked", {})
        if not isinstance(global_blocked, dict):
            global_blocked = {}
        global_blocked[cap_id] = entry
        state_doc["global_blocked"] = global_blocked
    else:
        group_blocked = state_doc.setdefault("group_blocked", {})
        if not isinstance(group_blocked, dict):
            group_blocked = {}
        per_group = group_blocked.setdefault(gid, {})
        if not isinstance(per_group, dict):
            per_group = {}
        per_group[cap_id] = entry
        group_blocked[gid] = per_group
        state_doc["group_blocked"] = group_blocked
    return entry


def _unset_blocked_capability(
    state_doc: Dict[str, Any],
    *,
    scope: str,
    group_id: str,
    capability_id: str,
) -> bool:
    cap_id = str(capability_id or "").strip()
    gid = str(group_id or "").strip()
    removed = False
    if scope == "global":
        global_blocked = state_doc.get("global_blocked") if isinstance(state_doc.get("global_blocked"), dict) else {}
        if cap_id in global_blocked:
            global_blocked.pop(cap_id, None)
            removed = True
        state_doc["global_blocked"] = global_blocked
        return removed

    group_blocked = state_doc.get("group_blocked") if isinstance(state_doc.get("group_blocked"), dict) else {}
    per_group = group_blocked.get(gid) if isinstance(group_blocked.get(gid), dict) else {}
    if cap_id in per_group:
        per_group.pop(cap_id, None)
        removed = True
    if per_group:
        group_blocked[gid] = per_group
    else:
        group_blocked.pop(gid, None)
    state_doc["group_blocked"] = group_blocked
    return removed


def _collect_blocked_capabilities(
    state_doc: Dict[str, Any],
    *,
    group_id: str,
) -> Tuple[Dict[str, Dict[str, str]], bool]:
    gid = str(group_id or "").strip()
    now = datetime.now(timezone.utc)
    blocked: Dict[str, Dict[str, str]] = {}
    mutated = False

    global_blocked = state_doc.get("global_blocked") if isinstance(state_doc.get("global_blocked"), dict) else {}
    group_blocked = state_doc.get("group_blocked") if isinstance(state_doc.get("group_blocked"), dict) else {}

    # Global baseline
    for cap_id, raw in list(global_blocked.items()):
        cid = str(cap_id or "").strip()
        if not cid or not isinstance(raw, dict):
            global_blocked.pop(cap_id, None)
            mutated = True
            continue
        expires_at = str(raw.get("expires_at") or "").strip()
        dt = parse_utc_iso(expires_at) if expires_at else None
        if dt is not None and dt <= now:
            global_blocked.pop(cap_id, None)
            mutated = True
            continue
        blocked[cid] = {
            "scope": "global",
            "reason": str(raw.get("reason") or "").strip(),
            "by": str(raw.get("by") or "").strip(),
            "blocked_at": str(raw.get("blocked_at") or "").strip(),
            "expires_at": expires_at,
        }

    # Group override / extension
    per_group = group_blocked.get(gid) if isinstance(group_blocked.get(gid), dict) else {}
    for cap_id, raw in list(per_group.items()):
        cid = str(cap_id or "").strip()
        if not cid or not isinstance(raw, dict):
            per_group.pop(cap_id, None)
            mutated = True
            continue
        expires_at = str(raw.get("expires_at") or "").strip()
        dt = parse_utc_iso(expires_at) if expires_at else None
        if dt is not None and dt <= now:
            per_group.pop(cap_id, None)
            mutated = True
            continue
        blocked[cid] = {
            "scope": "group",
            "reason": str(raw.get("reason") or "").strip(),
            "by": str(raw.get("by") or "").strip(),
            "blocked_at": str(raw.get("blocked_at") or "").strip(),
            "expires_at": expires_at,
        }
    if per_group:
        group_blocked[gid] = per_group
    elif gid in group_blocked:
        group_blocked.pop(gid, None)
        mutated = True

    if mutated:
        state_doc["global_blocked"] = global_blocked
        state_doc["group_blocked"] = group_blocked
    return blocked, mutated


def _has_any_binding_for_capability(
    state_doc: Dict[str, Any],
    *,
    capability_id: str,
) -> bool:
    cap_id = str(capability_id or "").strip()
    if not cap_id:
        return False
    group_enabled = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
    for items in group_enabled.values() if isinstance(group_enabled, dict) else []:
        if cap_id in set(items if isinstance(items, list) else []):
            return True
    actor_enabled = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
    for per_group in actor_enabled.values() if isinstance(actor_enabled, dict) else []:
        if not isinstance(per_group, dict):
            continue
        for items in per_group.values():
            if cap_id in set(items if isinstance(items, list) else []):
                return True
    session_enabled = state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
    for per_group in session_enabled.values() if isinstance(session_enabled, dict) else []:
        if not isinstance(per_group, dict):
            continue
        for entries in per_group.values():
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("capability_id") or "").strip() == cap_id:
                    return True
    return False


def _build_builtin_search_records() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for cap_id in all_builtin_pack_ids():
        pack = BUILTIN_CAPABILITY_PACKS.get(cap_id, {})
        out.append(
            {
                "capability_id": cap_id,
                "kind": "mcp_toolpack",
                "name": str(pack.get("title") or cap_id),
                "description_short": str(pack.get("description") or ""),
                "tags": list(pack.get("tags") or []),
                "source_id": "cccc_builtin",
                "source_tier": "builtin",
                "source_uri": "",
                "trust_tier": "builtin",
                "qualification_status": "qualified",
                "sync_state": "fresh",
                "enable_supported": True,
                "tool_count": len(tuple(pack.get("tool_names") or ())),
                "tool_names": [str(x).strip() for x in tuple(pack.get("tool_names") or ()) if str(x).strip()],
            }
        )
    return out


def _search_matches(query: str, item: Dict[str, Any]) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    fields = [
        str(item.get("capability_id") or ""),
        str(item.get("name") or ""),
        str(item.get("description_short") or ""),
        " ".join(str(x) for x in (item.get("tags") or [])),
    ]
    haystack = " ".join(fields).lower()
    return q in haystack


def _tokenize_search_text(text: str) -> List[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return []
    return [tok for tok in re.findall(r"[a-z0-9]{3,}", raw) if tok]


def _score_item_tokens(item: Dict[str, Any], tokens: List[str]) -> int:
    if not tokens:
        return 0
    fields = [
        str(item.get("capability_id") or ""),
        str(item.get("name") or ""),
        str(item.get("description_short") or ""),
        " ".join(str(x) for x in (item.get("tags") or [])),
        " ".join(str(x) for x in (item.get("tool_names") or [])),
    ]
    haystack = " ".join(fields).lower()
    if not haystack:
        return 0
    score = 0
    for tok in tokens:
        if tok in haystack:
            score += 1
    return score


def _canonicalize_actor_hint(actor_id: str) -> str:
    s = str(actor_id or "").strip()
    if not s:
        return ""
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", s)
    s = s.replace("_", "-").replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s.lower()


def _context_search_tokens(*, group_id: str, actor_id: str) -> List[str]:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        return []
    group = load_group(gid)
    if group is None:
        return []
    try:
        storage = ContextStorage(group)
        tasks = storage.list_tasks()
        task_by_id = {str(t.id or "").strip(): t for t in tasks}
        presence = storage.load_presence()

        actor_norm = _canonicalize_actor_hint(aid)
        actor_states = [
            a
            for a in presence.agents
            if str(getattr(a, "id", "") or "").strip() in {aid, actor_norm}
        ]
        task_focus_text: List[str] = []
        for st in actor_states:
            active_task_id = str(getattr(st, "active_task_id", "") or "").strip()
            if active_task_id:
                task = task_by_id.get(active_task_id)
                if task is not None:
                    task_focus_text.append(str(getattr(task, "name", "") or ""))
                    task_focus_text.append(str(getattr(task, "goal", "") or ""))
            task_focus_text.append(str(getattr(st, "focus", "") or ""))

        if not task_focus_text:
            for t in tasks:
                if str(getattr(t, "assignee", "") or "").strip() in {aid, actor_norm}:
                    task_focus_text.append(str(getattr(t, "name", "") or ""))
                    task_focus_text.append(str(getattr(t, "goal", "") or ""))

        out: List[str] = []
        seen: set[str] = set()
        for txt in task_focus_text:
            for tok in _tokenize_search_text(txt):
                if tok in seen:
                    continue
                seen.add(tok)
                out.append(tok)
                if len(out) >= 20:
                    return out
        return out
    except Exception:
        return []


def _role_preferred_pack_ids(actor_role: str) -> List[str]:
    role = str(actor_role or "").strip().lower()
    if role == "foreman":
        return [
            "pack:group-runtime",
            "pack:automation",
            "pack:context-advanced",
        ]
    if role == "peer":
        return [
            "pack:space",
            "pack:file-im",
        ]
    return [
        "pack:group-runtime",
        "pack:space",
    ]


def _render_source_states(catalog_doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sources_raw = catalog_doc.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for source_id in _SOURCE_IDS:
        state_raw = sources.get(source_id)
        state = dict(state_raw) if isinstance(state_raw, dict) else _source_state_template("never")
        state["staleness_seconds"] = _catalog_staleness_seconds(str(state.get("last_synced_at") or ""))
        out[source_id] = state
    return out


def _normalize_capability_id_list(raw: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        cap_id = str(item or "").strip()
        if not cap_id or cap_id in seen:
            continue
        seen.add(cap_id)
        out.append(cap_id)
    return out


def _normalize_profile_capability_defaults(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"autoload_capabilities": [], "default_scope": "actor", "session_ttl_seconds": 3600}
    scope = str(raw.get("default_scope") or "actor").strip().lower()
    if scope not in {"actor", "session"}:
        scope = "actor"
    try:
        ttl = int(raw.get("session_ttl_seconds") or 3600)
    except Exception:
        ttl = 3600
    ttl = max(60, min(ttl, 24 * 3600))
    autoload = _normalize_capability_id_list(raw.get("autoload_capabilities"))
    return {"autoload_capabilities": autoload, "default_scope": scope, "session_ttl_seconds": ttl}


def apply_actor_profile_capability_defaults(
    *,
    group_id: str,
    actor_id: str,
    profile_id: str,
    capability_defaults: Any,
) -> Dict[str, Any]:
    cfg = _normalize_profile_capability_defaults(capability_defaults)
    requested = list(cfg.get("autoload_capabilities") or [])
    scope = str(cfg.get("default_scope") or "actor")
    ttl_seconds = int(cfg.get("session_ttl_seconds") or 3600)
    if not requested:
        return {
            "requested_count": 0,
            "applied": [],
            "skipped": [],
            "scope": scope,
            "ttl_seconds": ttl_seconds,
        }

    applied: List[str] = []
    skipped: List[Dict[str, str]] = []
    for cap_id in requested:
        resp = handle_capability_enable(
            {
                "group_id": group_id,
                "by": "user",
                "actor_id": actor_id,
                "capability_id": cap_id,
                "scope": scope,
                "enabled": True,
                "ttl_seconds": ttl_seconds,
                "reason": f"profile_default:{profile_id}",
            }
        )
        if not bool(resp.ok):
            code = str((resp.error.code if resp.error else "") or "enable_failed")
            skipped.append({"capability_id": cap_id, "reason": code})
            continue
        result = resp.result if isinstance(resp.result, dict) else {}
        state = str(result.get("state") or "").strip().lower()
        if state == "ready" and bool(result.get("enabled", True)):
            applied.append(cap_id)
            continue
        reason = str(result.get("reason") or state or "not_ready")
        skipped.append({"capability_id": cap_id, "reason": reason})
    return {
        "requested_count": len(requested),
        "applied": applied,
        "skipped": skipped,
        "scope": scope,
        "ttl_seconds": ttl_seconds,
    }


def apply_actor_capability_autoload(
    *,
    group_id: str,
    actor_id: str,
    autoload_capabilities: Any,
    scope: str = "actor",
    ttl_seconds: int = 3600,
    reason: str = "actor_autoload",
) -> Dict[str, Any]:
    requested = _normalize_capability_id_list(autoload_capabilities)
    eff_scope = str(scope or "actor").strip().lower()
    if eff_scope not in {"actor", "session"}:
        eff_scope = "actor"
    try:
        eff_ttl = int(ttl_seconds or 3600)
    except Exception:
        eff_ttl = 3600
    eff_ttl = max(60, min(eff_ttl, 24 * 3600))
    if not requested:
        return {
            "requested_count": 0,
            "applied": [],
            "skipped": [],
            "scope": eff_scope,
            "ttl_seconds": eff_ttl,
        }

    applied: List[str] = []
    skipped: List[Dict[str, str]] = []
    for cap_id in requested:
        resp = handle_capability_enable(
            {
                "group_id": group_id,
                "by": "user",
                "actor_id": actor_id,
                "capability_id": cap_id,
                "scope": eff_scope,
                "enabled": True,
                "ttl_seconds": eff_ttl,
                "reason": reason,
            }
        )
        if not bool(resp.ok):
            code = str((resp.error.code if resp.error else "") or "enable_failed")
            skipped.append({"capability_id": cap_id, "reason": code})
            continue
        result = resp.result if isinstance(resp.result, dict) else {}
        state = str(result.get("state") or "").strip().lower()
        if state == "ready" and bool(result.get("enabled", True)):
            applied.append(cap_id)
            continue
        reason_code = str(result.get("reason") or state or "not_ready")
        skipped.append({"capability_id": cap_id, "reason": reason_code})
    return {
        "requested_count": len(requested),
        "applied": applied,
        "skipped": skipped,
        "scope": eff_scope,
        "ttl_seconds": eff_ttl,
    }


def handle_capability_overview(args: Dict[str, Any]) -> DaemonResponse:
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 400), 2000))
    include_indexed = bool(args.get("include_indexed", True))

    try:
        with _POLICY_LOCK:
            snapshot = _allowlist_effective_snapshot()
            policy = _allowlist_policy()
        effective_doc = snapshot.get("effective") if isinstance(snapshot.get("effective"), dict) else {}
        source_levels = policy.get("source_levels") if isinstance(policy.get("source_levels"), dict) else {}

        with _CATALOG_LOCK:
            catalog_path, catalog_doc = _load_catalog_doc()
            if _ensure_curated_catalog_records(catalog_doc, policy=policy):
                _save_catalog_doc(catalog_path, catalog_doc)
            source_states = _render_source_states(catalog_doc)
            records = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            external_rows = [dict(v) for v in records.values() if isinstance(v, dict)]

        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            blocked_caps_all, blocked_mutated = _collect_blocked_capabilities(state_doc, group_id="")
            if blocked_mutated:
                _save_state_doc(state_path, state_doc)

        blocked_global: Dict[str, Dict[str, Any]] = {}
        for cap_id, row in blocked_caps_all.items():
            if not isinstance(row, dict):
                continue
            if str(row.get("scope") or "").strip().lower() != "global":
                continue
            blocked_global[str(cap_id)] = dict(row)

        with _RUNTIME_LOCK:
            _, runtime_doc = _load_runtime_doc()
            capability_artifacts = _runtime_capability_artifacts(runtime_doc)
            artifacts = _runtime_artifacts(runtime_doc)
            recent_success = _runtime_recent_success(runtime_doc)

        entries: Dict[str, Dict[str, Any]] = {}

        def _upsert_entry(row: Dict[str, Any]) -> None:
            cap_id = str(row.get("capability_id") or "").strip()
            if not cap_id:
                return
            current = entries.get(cap_id) if isinstance(entries.get(cap_id), dict) else {}
            merged = dict(current)
            merged.update({k: v for k, v in row.items() if v not in (None, "", [], {}) or k not in merged})
            merged["capability_id"] = cap_id
            entries[cap_id] = merged

        for row in _build_builtin_search_records():
            if isinstance(row, dict):
                _upsert_entry(row)
        for row in external_rows:
            if isinstance(row, dict):
                _upsert_entry(row)

        for cap_id, row in recent_success.items():
            cid = str(cap_id or "").strip()
            if not cid:
                continue
            if cid in entries:
                continue
            kind = "skill" if cid.startswith("skill:") else ("mcp_toolpack" if cid.startswith("mcp:") else "")
            _upsert_entry(
                {
                    "capability_id": cid,
                    "kind": kind,
                    "name": _display_name_from_capability_id(cid),
                    "description_short": "Recently successful capability",
                    "source_id": "runtime_recent_success",
                    "sync_state": "runtime",
                    "qualification_status": _QUAL_QUALIFIED,
                }
            )

        source_cfg_map: Dict[str, Dict[str, Any]] = {}
        effective_sources = effective_doc.get("sources") if isinstance(effective_doc.get("sources"), list) else []
        for row in effective_sources:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("source_id") or "").strip()
            if not sid:
                continue
            source_cfg_map[sid] = {
                "enabled": bool(row.get("enabled", True)),
                "rationale": str(row.get("rationale") or "").strip(),
            }

        rows: List[Dict[str, Any]] = []
        for cap_id, rec in entries.items():
            kind = str(rec.get("kind") or "").strip()
            source_id = str(rec.get("source_id") or "").strip()
            policy_level = _effective_policy_level(
                policy,
                capability_id=cap_id,
                kind=kind,
                source_id=source_id,
                actor_role="",
            )
            if (not include_indexed) and (not _policy_level_visible(policy_level)):
                continue
            blocked = blocked_global.get(cap_id) if isinstance(blocked_global.get(cap_id), dict) else None
            enable_supported = _record_enable_supported(rec, capability_id=cap_id)
            recent_row = recent_success.get(cap_id) if isinstance(recent_success.get(cap_id), dict) else {}
            artifact_id = str(capability_artifacts.get(cap_id) or "").strip()
            artifact = artifacts.get(artifact_id) if artifact_id and isinstance(artifacts.get(artifact_id), dict) else {}
            install_state = str(artifact.get("state") or "").strip()
            install_error_code = str(artifact.get("last_error_code") or "").strip()
            install_error = str(artifact.get("last_error") or "").strip()
            recent_payload: Dict[str, Any] = {}
            if recent_row:
                recent_payload = {
                    "success_count": int(recent_row.get("success_count") or 0),
                    "last_success_at": str(recent_row.get("last_success_at") or ""),
                    "last_group_id": str(recent_row.get("last_group_id") or ""),
                    "last_actor_id": str(recent_row.get("last_actor_id") or ""),
                    "last_action": str(recent_row.get("last_action") or ""),
                }
            blocked_reason = str((blocked or {}).get("reason") or "").strip() if isinstance(blocked, dict) else ""
            item: Dict[str, Any] = {
                "capability_id": cap_id,
                "kind": kind,
                "name": str(rec.get("name") or _display_name_from_capability_id(cap_id)),
                "description_short": str(rec.get("description_short") or ""),
                "source_id": source_id,
                "source_uri": str(rec.get("source_uri") or ""),
                "source_tier": str(rec.get("source_tier") or ""),
                "trust_tier": str(rec.get("trust_tier") or ""),
                "license": str(rec.get("license") or ""),
                "sync_state": str(rec.get("sync_state") or ""),
                "policy_level": policy_level,
                "enable_supported": bool(enable_supported),
                "qualification_status": str(rec.get("qualification_status") or _QUAL_QUALIFIED),
                "install_mode": str(rec.get("install_mode") or ""),
                "tags": [str(x).strip() for x in (rec.get("tags") if isinstance(rec.get("tags"), list) else []) if str(x).strip()],
                "blocked_global": bool(blocked),
                "autoload_candidate": bool(enable_supported and (not blocked) and (_policy_level_visible(policy_level) or bool(recent_payload))),
                "policy_visible": bool(_policy_level_visible(policy_level)),
            }
            if blocked_reason:
                item["blocked_reason"] = blocked_reason
            if recent_payload:
                item["recent_success"] = recent_payload
            if install_state:
                item["cached_install_state"] = install_state
            if install_error_code:
                item["cached_install_error_code"] = install_error_code
            if install_error:
                item["cached_install_error"] = install_error
            if cap_id.startswith("pack:"):
                item["tool_count"] = int(rec.get("tool_count") or 0)
                tool_names = rec.get("tool_names") if isinstance(rec.get("tool_names"), list) else []
                if tool_names:
                    item["tool_names"] = [str(x).strip() for x in tool_names if str(x).strip()]
            rows.append(item)

        if query:
            rows = [row for row in rows if _search_matches(query, row)]

        def _rank(row: Dict[str, Any]) -> Tuple[int, int, int, str]:
            blocked_penalty = 1 if bool(row.get("blocked_global")) else 0
            recent = row.get("recent_success") if isinstance(row.get("recent_success"), dict) else {}
            recent_count = int(recent.get("success_count") or 0)
            recent_ts = str(recent.get("last_success_at") or "")
            cap_id = str(row.get("capability_id") or "")
            builtin_bias = 0 if cap_id.startswith("pack:") else 1
            return (
                blocked_penalty,
                builtin_bias,
                -recent_count,
                f"{recent_ts}:{str(row.get('name') or cap_id).lower()}",
            )

        rows.sort(key=_rank)
        items = rows[:limit]

        source_ids = sorted(
            {
                *_SOURCE_IDS,
                *(source_states.keys() if isinstance(source_states, dict) else []),
                *(source_levels.keys() if isinstance(source_levels, dict) else []),
                *(source_cfg_map.keys() if isinstance(source_cfg_map, dict) else []),
            }
        )
        sources: Dict[str, Dict[str, Any]] = {}
        for source_id in source_ids:
            state = source_states.get(source_id) if isinstance(source_states.get(source_id), dict) else {}
            cfg = source_cfg_map.get(source_id) if isinstance(source_cfg_map.get(source_id), dict) else {}
            sources[source_id] = {
                "source_id": source_id,
                "enabled": bool(cfg.get("enabled", True)),
                "source_level": _normalize_policy_level(source_levels.get(source_id), default=_LEVEL_INDEXED),
                "rationale": str(cfg.get("rationale") or ""),
                "sync_state": str(state.get("sync_state") or "never"),
                "last_synced_at": str(state.get("last_synced_at") or ""),
                "staleness_seconds": int(state.get("staleness_seconds") or 0),
                "record_count": int(state.get("record_count") or 0),
                "error": str(state.get("error") or ""),
            }

        blocked_list: List[Dict[str, Any]] = []
        for cap_id, row in sorted(blocked_global.items(), key=lambda x: str(x[0])):
            blocked_list.append(
                {
                    "capability_id": str(cap_id),
                    "scope": "global",
                    "reason": str(row.get("reason") or ""),
                    "by": str(row.get("by") or ""),
                    "blocked_at": str(row.get("blocked_at") or ""),
                    "expires_at": str(row.get("expires_at") or ""),
                }
            )

        return DaemonResponse(
            ok=True,
            result={
                "items": items,
                "count": len(items),
                "query": query,
                "sources": sources,
                "blocked_capabilities": blocked_list,
                "allowlist_revision": str(snapshot.get("revision") or ""),
            },
        )
    except Exception as e:
        return _error("capability_overview_failed", str(e))


def handle_capability_search(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or args.get("by") or "").strip()
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 30), 200))
    include_external = bool(args.get("include_external", True))
    kind_filter = str(args.get("kind") or "").strip().lower()
    source_filter = str(args.get("source_id") or "").strip()
    trust_filter = str(args.get("trust_tier") or "").strip().lower()
    qualification_filter = str(args.get("qualification_status") or "").strip().lower()

    try:
        group = _ensure_group(group_id)
        if actor_id and actor_id != "user":
            # If actor context is provided, ensure actor exists in this group.
            actors = group.doc.get("actors") if isinstance(group.doc.get("actors"), list) else []
            if actor_id not in {str(a.get("id") or "") for a in actors if isinstance(a, dict)}:
                return _error("actor_not_found", f"actor not found in group: {actor_id}")

        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            enabled_caps, mutated = _collect_enabled_capabilities(
                state_doc, group_id=group_id, actor_id=actor_id or "user"
            )
            blocked_caps, blocked_mutated = _collect_blocked_capabilities(state_doc, group_id=group_id)
            if mutated or blocked_mutated:
                _save_state_doc(state_path, state_doc)
        enabled_set = set(enabled_caps)
        actor_role = _resolve_actor_role(group, actor_id)
        policy = _allowlist_policy()
        query_tokens = _tokenize_search_text(query)
        context_tokens = _context_search_tokens(group_id=group_id, actor_id=actor_id)
        preferred_packs = set(_role_preferred_pack_ids(actor_role))

        external_records: List[Dict[str, Any]] = []
        source_states: Dict[str, Dict[str, Any]] = {}
        remote_augmented = False
        remote_added = 0
        remote_error = ""
        with _CATALOG_LOCK:
            catalog_path, catalog_doc = _load_catalog_doc()
            if include_external and _ensure_curated_catalog_records(catalog_doc, policy=policy):
                _save_catalog_doc(catalog_path, catalog_doc)
            if include_external:
                for item in (
                    catalog_doc.get("records")
                    if isinstance(catalog_doc.get("records"), dict)
                    else {}
                ).values():
                    if isinstance(item, dict):
                        external_records.append(dict(item))
            source_states = _render_source_states(catalog_doc)

        records: List[Dict[str, Any]] = _build_builtin_search_records()
        if include_external:
            records.extend(external_records)

        if kind_filter:
            records = [r for r in records if str(r.get("kind") or "").strip().lower() == kind_filter]
        if source_filter:
            records = [r for r in records if str(r.get("source_id") or "").strip() == source_filter]
        if trust_filter:
            records = [r for r in records if str(r.get("trust_tier") or "").strip().lower() == trust_filter]
        if qualification_filter:
            records = [
                r
                for r in records
                if str(r.get("qualification_status") or "").strip().lower() == qualification_filter
            ]
        policy_hidden_count = 0
        visible_records: List[Dict[str, Any]] = []
        for rec in records:
            cap_id = str(rec.get("capability_id") or "").strip()
            policy_level = _effective_policy_level(
                policy,
                capability_id=cap_id,
                kind=str(rec.get("kind") or ""),
                source_id=str(rec.get("source_id") or ""),
                actor_role=actor_role,
            )
            next_rec = dict(rec)
            next_rec["policy_level"] = policy_level
            block_entry = blocked_caps.get(cap_id) if isinstance(blocked_caps.get(cap_id), dict) else None
            if isinstance(block_entry, dict):
                next_rec["qualification_status"] = _QUAL_BLOCKED
                reasons = list(next_rec.get("qualification_reasons") or [])
                reason_text = str(block_entry.get("reason") or "").strip()
                scope_text = str(block_entry.get("scope") or "").strip().lower()
                if reason_text:
                    reasons = [f"runtime_block:{reason_text}"]
                else:
                    reasons = [f"runtime_block_{scope_text or 'group'}"]
                next_rec["qualification_reasons"] = reasons
                next_rec["blocked_scope"] = scope_text or "group"
            if (not _policy_level_visible(policy_level)) and (cap_id not in enabled_set):
                policy_hidden_count += 1
                continue
            visible_records.append(next_rec)
        records = visible_records
        records = [r for r in records if _search_matches(query, r)]

        remote_limit = max(1, min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_FALLBACK_LIMIT", 40), 100))
        target_fill = max(1, min(limit, remote_limit))
        should_remote_augment = (
            include_external
            and bool(str(query or "").strip())
            and len(records) < target_fill
            and kind_filter in {"", "mcp_toolpack", "skill"}
            and _env_bool("CCCC_CAPABILITY_SEARCH_REMOTE_FALLBACK", True)
        )
        if should_remote_augment:
            remote_rows: List[Dict[str, Any]] = []
            remote_errors: List[str] = []
            needed = max(1, target_fill - len(records))
            try:
                if kind_filter in {"", "mcp_toolpack"} and _env_bool("CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED", True):
                    remote_rows.extend(_remote_search_mcp_registry_records(query=query, limit=needed))
            except Exception as e:
                remote_errors.append(f"mcp_registry:{e}")
            try:
                if kind_filter in {"", "skill"}:
                    skill_limit = max(
                        1,
                        min(_env_int("CCCC_CAPABILITY_SEARCH_REMOTE_SKILL_LIMIT", remote_limit), 100),
                    )
                    remote_rows.extend(
                        _remote_search_skill_records(
                            query=query,
                            limit=min(needed, skill_limit),
                            source_filter=source_filter,
                        )
                    )
            except Exception as e:
                remote_errors.append(f"skills:{e}")

            if remote_rows:
                existing = {str(r.get("capability_id") or "") for r in records if isinstance(r, dict)}
                accepted: List[Dict[str, Any]] = []
                for rec in remote_rows:
                    if kind_filter and str(rec.get("kind") or "").strip().lower() != kind_filter:
                        continue
                    if source_filter and str(rec.get("source_id") or "").strip() != source_filter:
                        continue
                    if trust_filter and str(rec.get("trust_tier") or "").strip().lower() != trust_filter:
                        continue
                    if qualification_filter and str(rec.get("qualification_status") or "").strip().lower() != qualification_filter:
                        continue
                    if not _search_matches(query, rec):
                        continue
                    cap_id = str(rec.get("capability_id") or "").strip()
                    if not cap_id or cap_id in existing:
                        continue
                    policy_level = _effective_policy_level(
                        policy,
                        capability_id=cap_id,
                        kind=str(rec.get("kind") or ""),
                        source_id=str(rec.get("source_id") or ""),
                        actor_role=actor_role,
                    )
                    if (not _policy_level_visible(policy_level)) and (cap_id not in enabled_set):
                        policy_hidden_count += 1
                        continue
                    existing.add(cap_id)
                    accepted_rec = dict(rec)
                    accepted_rec["policy_level"] = policy_level
                    block_entry = blocked_caps.get(cap_id) if isinstance(blocked_caps.get(cap_id), dict) else None
                    if isinstance(block_entry, dict):
                        accepted_rec["qualification_status"] = _QUAL_BLOCKED
                        reason_text = str(block_entry.get("reason") or "").strip()
                        scope_text = str(block_entry.get("scope") or "").strip().lower()
                        accepted_rec["qualification_reasons"] = (
                            [f"runtime_block:{reason_text}"]
                            if reason_text
                            else [f"runtime_block_{scope_text or 'group'}"]
                        )
                        accepted_rec["blocked_scope"] = scope_text or "group"
                    accepted.append(accepted_rec)
                if accepted:
                    records.extend(accepted)
                    remote_augmented = True
                    remote_added = len(accepted)
                    with _CATALOG_LOCK:
                        path, doc = _load_catalog_doc()
                        rows = doc.get("records") if isinstance(doc.get("records"), dict) else {}
                        changed = False
                        touched_sources: set[str] = set()
                        for rec in accepted:
                            cap_id = str(rec.get("capability_id") or "").strip()
                            if not cap_id:
                                continue
                            store_rec = dict(rec)
                            store_rec.pop("blocked_scope", None)
                            rec_reasons = store_rec.get("qualification_reasons")
                            reasons = (
                                [str(x).strip() for x in rec_reasons if str(x).strip()]
                                if isinstance(rec_reasons, list)
                                else []
                            )
                            if (
                                str(store_rec.get("qualification_status") or "").strip().lower() == _QUAL_BLOCKED
                                and any(r.startswith("runtime_block") for r in reasons)
                            ):
                                store_rec["qualification_status"] = _QUAL_QUALIFIED
                                store_rec["qualification_reasons"] = []
                            source_id = str(store_rec.get("source_id") or "").strip()
                            if source_id:
                                touched_sources.add(source_id)
                            if rows.get(cap_id) != store_rec:
                                rows[cap_id] = store_rec
                                changed = True
                        if changed:
                            doc["records"] = rows
                            now_iso = utc_now_iso()
                            sources_doc = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
                            for source_id in touched_sources:
                                state = (
                                    sources_doc.get(source_id)
                                    if isinstance(sources_doc.get(source_id), dict)
                                    else _source_state_template("never")
                                )
                                state["sync_state"] = "remote_fallback"
                                state["last_synced_at"] = now_iso
                                state["staleness_seconds"] = 0
                                state["error"] = ""
                                sources_doc[source_id] = state
                            doc["sources"] = sources_doc
                            _refresh_source_record_counts(doc)
                            _save_catalog_doc(path, doc)
                        source_states = _render_source_states(doc)
            if remote_errors:
                remote_error = "; ".join(str(x) for x in remote_errors if str(x))

        def _rank(item: Dict[str, Any]) -> Tuple[int, int, int, int, int, str]:
            cap_id = str(item.get("capability_id") or "")
            is_builtin = 0 if cap_id.startswith("pack:") else 1
            name_key = str(item.get("name") or cap_id).lower()
            enabled_bias = 0 if cap_id in enabled_set else 1
            qualification = str(item.get("qualification_status") or "").strip().lower()
            if qualification == _QUAL_BLOCKED:
                qualification_penalty = 2
            elif qualification == _QUAL_UNAVAILABLE:
                qualification_penalty = 1
            else:
                qualification_penalty = 0

            # Query-first path: preserve deterministic behavior with stronger lexical score.
            if query_tokens:
                query_score = _score_item_tokens(item, query_tokens)
                return (
                    is_builtin,
                    enabled_bias,
                    -query_score,
                    qualification_penalty,
                    0,
                    name_key,
                )

            # Empty-query path: prioritize actionable + context-relevant packs.
            context_score = _score_item_tokens(item, context_tokens)
            preferred_bias = 0 if cap_id in preferred_packs else 1
            return (
                is_builtin,
                enabled_bias,
                -context_score,
                preferred_bias,
                qualification_penalty,
                name_key,
            )

        records.sort(key=_rank)
        sliced = records[:limit]
        items: List[Dict[str, Any]] = []
        for rec in sliced:
            cap_id = str(rec.get("capability_id") or "")
            item = {
                "capability_id": cap_id,
                "kind": str(rec.get("kind") or ""),
                "name": str(rec.get("name") or cap_id),
                "description_short": str(rec.get("description_short") or ""),
                "source_id": str(rec.get("source_id") or ""),
                "source_tier": str(rec.get("source_tier") or ""),
                "source_uri": str(rec.get("source_uri") or ""),
                "trust_tier": str(rec.get("trust_tier") or ""),
                "license": str(rec.get("license") or ""),
                "qualification_status": str(rec.get("qualification_status") or _QUAL_QUALIFIED),
                "sync_state": str(rec.get("sync_state") or ""),
                "enabled": cap_id in enabled_set,
                "enable_supported": _record_enable_supported(rec, capability_id=cap_id),
                "install_mode": str(rec.get("install_mode") or ""),
                "policy_level": str(rec.get("policy_level") or ""),
                "tags": list(rec.get("tags") or []),
            }
            if cap_id.startswith("pack:"):
                item["tool_count"] = int(rec.get("tool_count") or 0)
                tool_names = rec.get("tool_names") if isinstance(rec.get("tool_names"), list) else []
                if tool_names:
                    item["tool_names"] = [str(x).strip() for x in tool_names if str(x).strip()]
            qualification = str(item.get("qualification_status") or "")
            if qualification == _QUAL_BLOCKED:
                item["enable_hint"] = "blocked"
                reasons = rec.get("qualification_reasons")
                if isinstance(reasons, list):
                    first = next((str(x).strip() for x in reasons if str(x).strip()), "")
                    if first:
                        item["blocked_reason"] = first
            elif bool(item.get("enable_supported")):
                item["enable_hint"] = "enable_now"
            else:
                item["enable_hint"] = "unsupported"
            blocked_scope = str(rec.get("blocked_scope") or "").strip().lower()
            if blocked_scope:
                item["blocked_scope"] = blocked_scope
            items.append(item)

        return DaemonResponse(
            ok=True,
            result={
                "group_id": group_id,
                "actor_id": actor_id,
                "default_profile": "core",
                "items": items,
                "count": len(items),
                "sources": source_states,
                "applied_filters": {
                    "kind": kind_filter,
                    "source_id": source_filter,
                    "trust_tier": trust_filter,
                    "qualification_status": qualification_filter,
                },
                "search_diagnostics": {
                    "remote_augmented": bool(remote_augmented),
                    "remote_added": int(remote_added),
                    "remote_error": str(remote_error or ""),
                    "policy_hidden_count": int(policy_hidden_count),
                },
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except Exception as e:
        return _error("capability_search_failed", str(e))


def handle_capability_enable(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or args.get("actor_id") or "").strip()
    actor_id = str(args.get("actor_id") or by).strip()
    capability_id = str(args.get("capability_id") or "").strip()
    enabled = bool(args.get("enabled", True))
    cleanup = bool(args.get("cleanup", False))
    ttl_seconds = int(args.get("ttl_seconds") or 3600)
    reason = str(args.get("reason") or "").strip()
    if len(reason) > 280:
        reason = reason[:280]
    action_id = f"cact_{uuid.uuid4().hex[:16]}"
    scope_for_audit = str(args.get("scope") or "session").strip().lower() or "session"
    max_enabled_per_actor = _quota_limit("CCCC_CAPABILITY_MAX_ENABLED_PER_ACTOR", 12, minimum=1, maximum=500)
    max_enabled_per_group = _quota_limit("CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP", 24, minimum=1, maximum=2000)
    max_installations_total = _quota_limit("CCCC_CAPABILITY_MAX_INSTALLATIONS_TOTAL", 128, minimum=1, maximum=5000)

    def _audit(
        outcome: str,
        *,
        state: str = "",
        error_code: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = dict(details) if isinstance(details, dict) else {}
        if state:
            payload["state"] = state
        if error_code:
            payload["error_code"] = error_code
        if reason:
            payload["reason"] = reason
        try:
            _append_audit_event(
                action_id=action_id,
                op="capability_enable",
                group_id=group_id,
                actor_id=actor_id,
                by=by,
                capability_id=capability_id,
                scope=scope_for_audit,
                enabled=enabled,
                outcome=outcome,
                details=payload,
            )
        except Exception:
            # Audit write failures should not block capability state mutation path.
            pass

    def _check_enable_quota(*, scope: str, group_id: str, actor_id: str, capability_id: str) -> str:
        with _STATE_LOCK:
            _, state_doc = _load_state_doc()
            if scope in {"actor", "session"}:
                enabled_caps, _ = _collect_enabled_capabilities(
                    state_doc,
                    group_id=group_id,
                    actor_id=actor_id or "user",
                )
                if capability_id not in set(enabled_caps) and len(enabled_caps) >= max_enabled_per_actor:
                    return f"quota_enabled_actor_exceeded:{max_enabled_per_actor}"

            if scope == "group":
                group_enabled = (
                    state_doc.get("group_enabled")
                    if isinstance(state_doc.get("group_enabled"), dict)
                    else {}
                )
                current = set(group_enabled.get(group_id) or [])
                if capability_id not in current and len(current) >= max_enabled_per_group:
                    return f"quota_enabled_group_exceeded:{max_enabled_per_group}"
        return ""

    try:
        group = _ensure_group(group_id)
        scope = _normalize_scope(args.get("scope"))
        scope_for_audit = scope
        policy = _allowlist_policy()
        actor_role = _resolve_actor_role(group, actor_id)
        if not capability_id:
            _audit("denied", state="denied", error_code="missing_capability_id")
            return _error("missing_capability_id", "missing capability_id", details={"action_id": action_id})

        # Permission model:
        # - user can write all scopes
        # - actors can write actor/session for self
        # - only foreman can write group scope
        if by != "user":
            if not by:
                _audit("denied", state="denied", error_code="missing_actor_id")
                return _error("missing_actor_id", "missing actor identity (by)", details={"action_id": action_id})
            if scope == "group" and (not _is_foreman(group, by)):
                _audit("denied", state="denied", error_code="permission_denied")
                return _error(
                    "permission_denied",
                    "only foreman can enable group scope",
                    details={"action_id": action_id},
                )
            if scope in {"actor", "session"} and actor_id != by:
                _audit("denied", state="denied", error_code="permission_denied")
                return _error(
                    "permission_denied",
                    "actor can only mutate their own actor/session scope",
                    details={"action_id": action_id},
                )

        blocked_entry: Dict[str, str] = {}
        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            blocked_caps, blocked_mutated = _collect_blocked_capabilities(state_doc, group_id=group_id)
            if blocked_mutated:
                _save_state_doc(state_path, state_doc)
        maybe_block = blocked_caps.get(capability_id) if isinstance(blocked_caps.get(capability_id), dict) else None
        if isinstance(maybe_block, dict):
            blocked_entry = dict(maybe_block)
        if enabled and blocked_entry:
            scope_token = str(blocked_entry.get("scope") or "group").strip().lower()
            reason_code = "blocked_by_global_policy" if scope_token == "global" else "blocked_by_group_policy"
            reason_text = str(blocked_entry.get("reason") or "").strip()
            details = {"blocked_scope": scope_token}
            if reason_text:
                details["blocked_reason"] = reason_text
            _audit("failed", state="failed", error_code=reason_code, details=details)
            result = {
                "action_id": action_id,
                "group_id": group_id,
                "actor_id": actor_id,
                "capability_id": capability_id,
                "scope": scope,
                "enabled": False,
                "state": "failed",
                "refresh_required": False,
                "reason": reason_code,
                "policy_level": "blocked",
                "blocked_scope": scope_token,
            }
            if reason_text:
                result["blocked_reason"] = reason_text
            return DaemonResponse(ok=True, result=result)

        if enabled:
            quota_reason = _check_enable_quota(
                scope=scope,
                group_id=group_id,
                actor_id=actor_id,
                capability_id=capability_id,
            )
            if quota_reason:
                _audit("failed", state="failed", error_code=quota_reason)
                return DaemonResponse(
                    ok=True,
                    result={
                        "action_id": action_id,
                        "group_id": group_id,
                        "actor_id": actor_id,
                        "capability_id": capability_id,
                        "scope": scope,
                        "enabled": False,
                        "state": "failed",
                        "refresh_required": False,
                        "reason": quota_reason,
                    },
                )

        # Built-in packs are directly enable-able.
        if capability_id.startswith("pack:"):
            if capability_id not in BUILTIN_CAPABILITY_PACKS:
                _audit("denied", state="denied", error_code="capability_not_found")
                return _error(
                    "capability_not_found",
                    f"unknown built-in capability: {capability_id}",
                    details={"action_id": action_id},
                )
            policy_level = _effective_policy_level(
                policy,
                capability_id=capability_id,
                kind="mcp_toolpack",
                source_id="cccc_builtin",
                actor_role=actor_role,
            )
            if enabled and (not _policy_level_visible(policy_level)):
                reason_code = "policy_level_indexed"
                _audit("failed", state="failed", error_code=reason_code, details={"policy_level": policy_level})
                return DaemonResponse(
                    ok=True,
                    result={
                        "action_id": action_id,
                        "group_id": group_id,
                        "actor_id": actor_id,
                        "capability_id": capability_id,
                        "scope": scope,
                        "enabled": False,
                        "state": "failed",
                        "refresh_required": False,
                        "reason": reason_code,
                        "policy_level": policy_level,
                    },
                )
            with _STATE_LOCK:
                state_path, state_doc = _load_state_doc()
                _set_enabled_capability(
                    state_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    scope=scope,
                    capability_id=capability_id,
                    enabled=enabled,
                    ttl_seconds=ttl_seconds,
                )
                _save_state_doc(state_path, state_doc)
            if enabled:
                with _RUNTIME_LOCK:
                    runtime_path, runtime_doc = _load_runtime_doc()
                    _record_runtime_recent_success(
                        runtime_doc,
                        capability_id=capability_id,
                        group_id=group_id,
                        actor_id=actor_id,
                        action="enable",
                    )
                    _save_runtime_doc(runtime_path, runtime_doc)
            _audit("ready", state="ready", details={"builtin": True})
            return DaemonResponse(
                ok=True,
                result={
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": enabled,
                    "state": "ready",
                    "refresh_required": True,
                    "wait": "relist_or_reconnect",
                    "refresh_mode": "relist_or_reconnect",
                    "policy_level": policy_level,
                },
            )

        # External capability path (M2): supports remote_only + package (npm/pypi/oci).
        with _CATALOG_LOCK:
            catalog_path, catalog_doc = _load_catalog_doc()
            if _ensure_curated_catalog_records(catalog_doc, policy=policy):
                _save_catalog_doc(catalog_path, catalog_doc)
            records = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
        rec = records.get(capability_id) if isinstance(records, dict) else None
        if not isinstance(rec, dict):
            if not enabled:
                removed_installation = False
                cleanup_skipped_reason = ""
                with _STATE_LOCK:
                    state_path, state_doc = _load_state_doc()
                    _set_enabled_capability(
                        state_doc,
                        group_id=group_id,
                        actor_id=actor_id,
                        scope=scope,
                        capability_id=capability_id,
                        enabled=False,
                        ttl_seconds=ttl_seconds,
                    )
                    has_remaining_binding = _has_any_binding_for_capability(state_doc, capability_id=capability_id)
                    _save_state_doc(state_path, state_doc)
                removed_binding_count = 0
                with _RUNTIME_LOCK:
                    runtime_path, runtime_doc = _load_runtime_doc()
                    if scope == "group":
                        removed_binding_count = _remove_runtime_group_capability_bindings(
                            runtime_doc,
                            group_id=group_id,
                            capability_id=capability_id,
                        )
                    else:
                        removed_binding_count = _remove_runtime_actor_binding(
                            runtime_doc,
                            group_id=group_id,
                            actor_id=actor_id,
                            capability_id=capability_id,
                        )
                    if cleanup:
                        if has_remaining_binding:
                            cleanup_skipped_reason = "cleanup_skipped_capability_still_bound"
                        else:
                            removed_artifact_id = _remove_runtime_capability_artifact(
                                runtime_doc,
                                capability_id=capability_id,
                            )
                            if removed_artifact_id:
                                removed_installation = _remove_runtime_artifact_if_unreferenced(
                                    runtime_doc,
                                    artifact_id=removed_artifact_id,
                                )
                    if removed_binding_count > 0:
                        _save_runtime_doc(runtime_path, runtime_doc)
                    elif cleanup and (removed_installation or cleanup_skipped_reason):
                        _save_runtime_doc(runtime_path, runtime_doc)
                _audit(
                    "ready",
                    state="ready",
                    details={
                        "external": True,
                        "disabled": True,
                        "record_missing": True,
                    },
                )
                result: Dict[str, Any] = {
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": False,
                    "state": "ready",
                    "refresh_required": True,
                    "wait": "relist_or_reconnect",
                    "refresh_mode": "relist_or_reconnect",
                    "removed_binding_count": int(removed_binding_count),
                    "removed_installation": bool(removed_installation),
                    "record_missing": True,
                }
                if cleanup_skipped_reason:
                    result["cleanup_skipped_reason"] = cleanup_skipped_reason
                return DaemonResponse(ok=True, result=result)
            if capability_id.startswith("mcp:") and _env_bool("CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED", True):
                server_name = capability_id.split(":", 1)[1]
                fetched: Optional[Dict[str, Any]] = None
                try:
                    fetched = _fetch_mcp_registry_record_by_server_name(server_name)
                except Exception:
                    fetched = None
                if isinstance(fetched, dict):
                    with _CATALOG_LOCK:
                        cpath, cdoc = _load_catalog_doc()
                        rows = cdoc.get("records") if isinstance(cdoc.get("records"), dict) else {}
                        rows[capability_id] = fetched
                        cdoc["records"] = rows
                        _refresh_source_record_counts(cdoc)
                        _save_catalog_doc(cpath, cdoc)
                    rec = fetched
            if not isinstance(rec, dict):
                _audit("denied", state="denied", error_code="capability_not_found")
                return _error(
                    "capability_not_found",
                    f"capability not found: {capability_id}",
                    details={"action_id": action_id},
                )

        policy_level = _effective_policy_level(
            policy,
            capability_id=capability_id,
            kind=str(rec.get("kind") or ""),
            source_id=str(rec.get("source_id") or ""),
            actor_role=actor_role,
        )
        if enabled and (not _policy_level_visible(policy_level)):
            reason_code = "policy_level_indexed"
            _audit("failed", state="failed", error_code=reason_code, details={"policy_level": policy_level})
            return DaemonResponse(
                ok=True,
                result={
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": False,
                    "state": "failed",
                    "refresh_required": False,
                    "reason": reason_code,
                    "policy_level": policy_level,
                },
            )

        if enabled and _needs_registry_hydration(capability_id, rec) and _env_bool(
            "CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED", True
        ):
            server_name = capability_id.split(":", 1)[1]
            fetched: Optional[Dict[str, Any]] = None
            try:
                fetched = _fetch_mcp_registry_record_by_server_name(server_name)
            except Exception:
                fetched = None
            if isinstance(fetched, dict):
                rec = _merge_registry_install_into_record(rec, fetched)
                with _CATALOG_LOCK:
                    cpath, cdoc = _load_catalog_doc()
                    rows = cdoc.get("records") if isinstance(cdoc.get("records"), dict) else {}
                    rows[capability_id] = rec
                    cdoc["records"] = rows
                    _refresh_source_record_counts(cdoc)
                    _save_catalog_doc(cpath, cdoc)

        if not enabled:
            removed_installation = False
            cleanup_skipped_reason = ""
            with _STATE_LOCK:
                state_path, state_doc = _load_state_doc()
                _set_enabled_capability(
                    state_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    scope=scope,
                    capability_id=capability_id,
                    enabled=False,
                    ttl_seconds=ttl_seconds,
                )
                has_remaining_binding = _has_any_binding_for_capability(state_doc, capability_id=capability_id)
                _save_state_doc(state_path, state_doc)
            removed_binding_count = 0
            with _RUNTIME_LOCK:
                runtime_path, runtime_doc = _load_runtime_doc()
                if scope == "group":
                    removed_binding_count = _remove_runtime_group_capability_bindings(
                        runtime_doc,
                        group_id=group_id,
                        capability_id=capability_id,
                    )
                else:
                    removed_binding_count = _remove_runtime_actor_binding(
                        runtime_doc,
                        group_id=group_id,
                        actor_id=actor_id,
                        capability_id=capability_id,
                    )
                if cleanup:
                    if has_remaining_binding:
                        cleanup_skipped_reason = "cleanup_skipped_capability_still_bound"
                    else:
                        removed_artifact_id = _remove_runtime_capability_artifact(
                            runtime_doc,
                            capability_id=capability_id,
                        )
                        if removed_artifact_id:
                            removed_installation = _remove_runtime_artifact_if_unreferenced(
                                runtime_doc,
                                artifact_id=removed_artifact_id,
                            )
                if removed_binding_count > 0:
                    _save_runtime_doc(runtime_path, runtime_doc)
                elif cleanup and (removed_installation or cleanup_skipped_reason):
                    _save_runtime_doc(runtime_path, runtime_doc)
            _audit("ready", state="ready", details={"external": True, "disabled": True})
            result: Dict[str, Any] = {
                "action_id": action_id,
                "group_id": group_id,
                "actor_id": actor_id,
                "capability_id": capability_id,
                "scope": scope,
                "enabled": False,
                "state": "ready",
                "refresh_required": True,
                "wait": "relist_or_reconnect",
                "refresh_mode": "relist_or_reconnect",
                "removed_binding_count": int(removed_binding_count),
                "removed_installation": bool(removed_installation),
                "policy_level": policy_level,
            }
            if cleanup_skipped_reason:
                result["cleanup_skipped_reason"] = cleanup_skipped_reason
            return DaemonResponse(
                ok=True,
                result=result,
            )

        qualification = str(rec.get("qualification_status") or _QUAL_QUALIFIED).strip().lower()
        if qualification == _QUAL_BLOCKED:
            _audit("denied", state="denied", error_code="qualification_blocked")
            return _error(
                "qualification_blocked",
                f"capability blocked by policy: {capability_id}",
                details={
                    "action_id": action_id,
                    "qualification_reasons": rec.get("qualification_reasons") or [],
                },
            )
        if enabled and (not _record_enable_supported(rec, capability_id=capability_id)):
            reason_code = "capability_unavailable"
            _audit("failed", state="failed", error_code=reason_code)
            return DaemonResponse(
                ok=True,
                result={
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": False,
                    "state": "failed",
                    "refresh_required": False,
                    "reason": reason_code,
                    "policy_level": policy_level,
                    "qualification_status": qualification or _QUAL_UNAVAILABLE,
                },
            )

        rec_kind = str(rec.get("kind") or "").strip().lower()
        if rec_kind == "skill":
            requires_caps = rec.get("requires_capabilities")
            requires = [str(x).strip() for x in requires_caps if str(x).strip()] if isinstance(requires_caps, list) else []
            applied_dependencies: List[str] = []
            skipped_dependencies: List[Dict[str, str]] = []
            with _STATE_LOCK:
                state_path, state_doc = _load_state_doc()
                _set_enabled_capability(
                    state_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    scope=scope,
                    capability_id=capability_id,
                    enabled=True,
                    ttl_seconds=ttl_seconds,
                )
                for dep_cap_id in requires:
                    if dep_cap_id not in BUILTIN_CAPABILITY_PACKS:
                        skipped_dependencies.append(
                            {"capability_id": dep_cap_id, "reason": "unsupported_skill_dependency"}
                        )
                        continue
                    dep_quota_reason = _check_enable_quota(
                        scope=scope,
                        group_id=group_id,
                        actor_id=actor_id,
                        capability_id=dep_cap_id,
                    )
                    if dep_quota_reason:
                        skipped_dependencies.append({"capability_id": dep_cap_id, "reason": dep_quota_reason})
                        continue
                    _set_enabled_capability(
                        state_doc,
                        group_id=group_id,
                        actor_id=actor_id,
                        scope=scope,
                        capability_id=dep_cap_id,
                        enabled=True,
                        ttl_seconds=ttl_seconds,
                    )
                    applied_dependencies.append(dep_cap_id)
                _save_state_doc(state_path, state_doc)

            with _RUNTIME_LOCK:
                runtime_path, runtime_doc = _load_runtime_doc()
                _set_runtime_actor_binding(
                    runtime_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    capability_id=capability_id,
                    state="ready_skill",
                    last_error="",
                )
                _record_runtime_recent_success(
                    runtime_doc,
                    capability_id=capability_id,
                    group_id=group_id,
                    actor_id=actor_id,
                    action="enable",
                )
                _save_runtime_doc(runtime_path, runtime_doc)

            capsule = str(rec.get("capsule_text") or "").strip()
            if not capsule:
                capsule = str(rec.get("description_short") or "").strip()
            refresh_required = bool(applied_dependencies)
            _audit(
                "ready",
                state="ready",
                details={
                    "skill": True,
                    "applied_dependencies": applied_dependencies,
                    "skipped_dependencies": skipped_dependencies,
                },
            )
            result: Dict[str, Any] = {
                "action_id": action_id,
                "group_id": group_id,
                "actor_id": actor_id,
                "capability_id": capability_id,
                "scope": scope,
                "enabled": True,
                "state": "ready",
                "refresh_required": refresh_required,
                "policy_level": policy_level,
                "skill": {
                    "capability_id": capability_id,
                    "name": str(rec.get("name") or capability_id),
                    "description_short": str(rec.get("description_short") or ""),
                    "capsule": capsule,
                    "requires_capabilities": requires,
                    "applied_dependencies": applied_dependencies,
                    "skipped_dependencies": skipped_dependencies,
                    "source_id": str(rec.get("source_id") or ""),
                    "source_uri": str(rec.get("source_uri") or ""),
                },
            }
            if refresh_required:
                result["wait"] = "relist_or_reconnect"
                result["refresh_mode"] = "relist_or_reconnect"
            return DaemonResponse(ok=True, result=result)

        supported, unsupported_reason = _supported_external_install_record(rec)
        if not supported:
            reason_code = unsupported_reason or "unsupported_external_installer"
            diagnostics = [
                {
                    "code": str(reason_code),
                    "message": str(reason_code),
                    "retryable": False,
                    "action_hints": ["switch_to_supported_installer_or_runtime"],
                }
            ]
            _audit("failed", state="failed", error_code=unsupported_reason or "unsupported_external_installer")
            return DaemonResponse(
                ok=True,
                result={
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": False,
                    "state": "failed",
                    "refresh_required": False,
                    "reason": reason_code,
                    "policy_level": policy_level,
                    "diagnostics": diagnostics,
                },
            )

        install: Dict[str, Any]
        reused_cached_install = False
        install_key = _external_artifact_cache_key(rec, capability_id=capability_id)
        artifact_id = _external_artifact_id(rec, capability_id=capability_id)
        with _RUNTIME_LOCK:
            _, runtime_doc_quota = _load_runtime_doc()
            artifacts_quota = _runtime_artifacts(runtime_doc_quota)
            mapped_artifact_id, existing_mapped = _runtime_install_for_capability(
                runtime_doc_quota,
                capability_id=capability_id,
            )
            if mapped_artifact_id:
                artifact_id = mapped_artifact_id
            existing = existing_mapped if isinstance(existing_mapped, dict) else artifacts_quota.get(artifact_id)
            if isinstance(existing, dict) and _install_state_allows_external_tool(existing.get("state")):
                install = dict(existing)
                reused_cached_install = True
            else:
                if artifact_id not in artifacts_quota and len(artifacts_quota) >= max_installations_total:
                    quota_reason = f"quota_installations_total_exceeded:{max_installations_total}"
                    _audit("failed", state="failed", error_code=quota_reason)
                    return DaemonResponse(
                        ok=True,
                        result={
                            "action_id": action_id,
                            "group_id": group_id,
                            "actor_id": actor_id,
                            "capability_id": capability_id,
                            "scope": scope,
                            "enabled": False,
                            "state": "failed",
                            "refresh_required": False,
                            "reason": quota_reason,
                            "policy_level": policy_level,
                        },
                    )
                install = {}

        if not reused_cached_install:
            try:
                install = _install_external_capability(rec, capability_id=capability_id)
            except Exception as e:
                install_error = _classify_external_install_error(e)
                error_code = str(install_error.get("code") or "install_failed")
                with _RUNTIME_LOCK:
                    runtime_path, runtime_doc = _load_runtime_doc()
                    failed_artifact = _artifact_entry_from_install(
                        {
                            "state": "install_failed",
                            "installer": "",
                            "install_mode": str(rec.get("install_mode") or ""),
                            "invoker": {},
                            "tools": [],
                            "last_error": str(e),
                            "last_error_code": error_code,
                            "updated_at": utc_now_iso(),
                        },
                        artifact_id=artifact_id,
                        install_key=install_key,
                        capability_id=capability_id,
                    )
                    _upsert_runtime_artifact_for_capability(
                        runtime_doc,
                        artifact_id=artifact_id,
                        capability_id=capability_id,
                        artifact_entry=failed_artifact,
                    )
                    _set_runtime_actor_binding(
                        runtime_doc,
                        group_id=group_id,
                        actor_id=actor_id,
                        capability_id=capability_id,
                        artifact_id=artifact_id,
                        state="install_failed",
                        last_error=str(e),
                    )
                    _save_runtime_doc(runtime_path, runtime_doc)
                _audit("failed", state="failed", error_code=error_code, details={"error": str(e)})
                reason = f"install_failed:{error_code}"
                result: Dict[str, Any] = {
                    "action_id": action_id,
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "capability_id": capability_id,
                    "scope": scope,
                    "enabled": False,
                    "state": "failed",
                    "refresh_required": False,
                    "reason": reason,
                    "install_error_code": error_code,
                    "retryable": bool(install_error.get("retryable")),
                    "policy_level": policy_level,
                    "diagnostics": _diagnostics_from_install_error(e),
                }
                required_env = install_error.get("required_env")
                if isinstance(required_env, list) and required_env:
                    result["required_env"] = [str(x).strip() for x in required_env if str(x).strip()]
                if str(e):
                    result["error"] = str(e)
                return DaemonResponse(
                    ok=True,
                    result=result,
                )

            with _RUNTIME_LOCK:
                runtime_path, runtime_doc = _load_runtime_doc()
                artifact = _artifact_entry_from_install(
                    install,
                    artifact_id=artifact_id,
                    install_key=install_key,
                    capability_id=capability_id,
                )
                _upsert_runtime_artifact_for_capability(
                    runtime_doc,
                    artifact_id=artifact_id,
                    capability_id=capability_id,
                    artifact_entry=artifact,
                )
                _set_runtime_actor_binding(
                    runtime_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    capability_id=capability_id,
                    artifact_id=artifact_id,
                    state="ready",
                    last_error="",
                )
                _save_runtime_doc(runtime_path, runtime_doc)
                install = artifact
        else:
            with _RUNTIME_LOCK:
                runtime_path, runtime_doc = _load_runtime_doc()
                _set_runtime_capability_artifact(
                    runtime_doc,
                    capability_id=capability_id,
                    artifact_id=artifact_id,
                )
                _set_runtime_actor_binding(
                    runtime_doc,
                    group_id=group_id,
                    actor_id=actor_id,
                    capability_id=capability_id,
                    artifact_id=artifact_id,
                    state="ready_cached",
                    last_error="",
                )
                _save_runtime_doc(runtime_path, runtime_doc)

        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            _set_enabled_capability(
                state_doc,
                group_id=group_id,
                actor_id=actor_id,
                scope=scope,
                capability_id=capability_id,
                enabled=True,
                ttl_seconds=ttl_seconds,
            )
            _save_state_doc(state_path, state_doc)
        with _RUNTIME_LOCK:
            runtime_path, runtime_doc = _load_runtime_doc()
            _record_runtime_recent_success(
                runtime_doc,
                capability_id=capability_id,
                group_id=group_id,
                actor_id=actor_id,
                action="enable",
            )
            _save_runtime_doc(runtime_path, runtime_doc)

        tools = install.get("tools") if isinstance(install.get("tools"), list) else []
        _audit("ready", state="ready", details={"installed_tool_count": len(tools)})
        install_state = str(install.get("state") or "").strip() or "installed"
        degraded = install_state == "installed_degraded"
        result: Dict[str, Any] = {
            "action_id": action_id,
            "group_id": group_id,
            "actor_id": actor_id,
            "capability_id": capability_id,
            "scope": scope,
            "enabled": True,
            "state": "ready",
            "refresh_required": True,
            "wait": "relist_or_reconnect",
            "refresh_mode": "relist_or_reconnect",
            "installed_tool_count": len(tools),
            "installer": str(install.get("installer") or ""),
            "reused_cached_install": bool(reused_cached_install),
            "policy_level": policy_level,
            "install_state": install_state,
            "degraded": degraded,
        }
        install_error_code = str(install.get("last_error_code") or "").strip()
        if install_error_code:
            result["install_error_code"] = install_error_code
        if degraded:
            degraded_reason = str(install.get("last_error") or "").strip()
            if degraded_reason:
                result["degraded_reason"] = degraded_reason
            result["degraded_call_hint"] = (
                "tools_not_listed_call_capability_use_with_capability_id_and_real_tool_name"
            )
        return DaemonResponse(
            ok=True,
            result=result,
        )
    except LookupError as e:
        _audit("failed", state="failed", error_code="group_not_found", details={"error": str(e)})
        return _error("group_not_found", str(e), details={"action_id": action_id})
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            _audit("failed", state="failed", error_code="missing_group_id")
            return _error("missing_group_id", "missing group_id", details={"action_id": action_id})
        _audit("failed", state="failed", error_code="capability_enable_invalid", details={"error": message})
        return _error("capability_enable_invalid", message, details={"action_id": action_id})
    except Exception as e:
        _audit("failed", state="failed", error_code="capability_enable_failed", details={"error": str(e)})
        return _error("capability_enable_failed", str(e), details={"action_id": action_id})


def handle_capability_state(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or args.get("actor_id") or "").strip()
    actor_id = str(args.get("actor_id") or by).strip()

    try:
        group = _ensure_group(group_id)
        if by and by != "user" and actor_id and actor_id != by:
            return _error("permission_denied", "actor can only inspect their own scope")
        if not actor_id:
            actor_id = "user"
        actor_role = _resolve_actor_role(group, actor_id)
        policy = _allowlist_policy()
        max_dynamic_tools_visible = _quota_limit(
            "CCCC_CAPABILITY_MAX_DYNAMIC_TOOLS_VISIBLE",
            32,
            minimum=1,
            maximum=500,
        )

        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            enabled_caps, mutated = _collect_enabled_capabilities(state_doc, group_id=group_id, actor_id=actor_id)
            blocked_caps, blocked_mutated = _collect_blocked_capabilities(state_doc, group_id=group_id)
            if mutated or blocked_mutated:
                _save_state_doc(state_path, state_doc)
        enabled_caps_effective = [cap for cap in enabled_caps if cap not in set(blocked_caps.keys())]
        builtin_enabled = [cap for cap in enabled_caps_effective if cap in BUILTIN_CAPABILITY_PACKS]
        external_enabled = [cap for cap in enabled_caps_effective if cap not in BUILTIN_CAPABILITY_PACKS]

        dynamic_tools: List[Dict[str, Any]] = []
        install_state_by_cap: Dict[str, str] = {}
        install_artifact_by_cap: Dict[str, str] = {}
        install_error_by_cap: Dict[str, str] = {}
        install_error_code_by_cap: Dict[str, str] = {}
        actor_binding_state_by_cap: Dict[str, Dict[str, str]] = {}
        with _RUNTIME_LOCK:
            _, runtime_doc = _load_runtime_doc()
            artifacts = _runtime_artifacts(runtime_doc)
            capability_artifacts = _runtime_capability_artifacts(runtime_doc)
            actor_bindings = _runtime_actor_bindings(runtime_doc)
            per_group_bindings = (
                actor_bindings.get(group_id)
                if isinstance(actor_bindings.get(group_id), dict)
                else {}
            )
            per_actor_bindings = (
                per_group_bindings.get(actor_id)
                if isinstance(per_group_bindings.get(actor_id), dict)
                else {}
            )
            if isinstance(capability_artifacts, dict):
                for cap_id, artifact_id in capability_artifacts.items():
                    cid = str(cap_id or "").strip()
                    aid = str(artifact_id or "").strip()
                    install = artifacts.get(aid) if isinstance(artifacts.get(aid), dict) else None
                    if not cid or not aid or not isinstance(install, dict):
                        continue
                    install_state_by_cap[cid] = str(install.get("state") or "").strip()
                    install_artifact_by_cap[cid] = aid
                    install_error_by_cap[cid] = str(install.get("last_error") or "").strip()
                    install_error_code_by_cap[cid] = str(install.get("last_error_code") or "").strip()
            if isinstance(per_actor_bindings, dict):
                for cap_id, item in per_actor_bindings.items():
                    if not isinstance(item, dict):
                        continue
                    actor_binding_state_by_cap[str(cap_id)] = {
                        "artifact_id": str(item.get("artifact_id") or "").strip(),
                        "state": str(item.get("state") or "").strip() or "unknown",
                        "last_error": str(item.get("last_error") or "").strip(),
                    }
            for capability_id in external_enabled:
                binding = per_actor_bindings.get(capability_id) if isinstance(per_actor_bindings, dict) else None
                binding_artifact_id = (
                    str((binding or {}).get("artifact_id") or "").strip()
                    if isinstance(binding, dict)
                    else ""
                )
                artifact_id = binding_artifact_id or str(install_artifact_by_cap.get(capability_id) or "").strip()
                install = artifacts.get(artifact_id) if isinstance(artifacts, dict) else None
                if not isinstance(install, dict):
                    continue
                if not _install_state_allows_external_tool(install.get("state")):
                    continue
                binding_state = str((binding or {}).get("state") or "").strip() if isinstance(binding, dict) else ""
                if not _binding_state_allows_external_tool(binding_state):
                    continue
                tools = install.get("tools") if isinstance(install.get("tools"), list) else []
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    name = str(tool.get("name") or "").strip()
                    real_name = str(tool.get("real_tool_name") or "").strip()
                    if not name or not real_name:
                        continue
                    schema = _normalize_mcp_input_schema(tool.get("inputSchema"))
                    dynamic_tools.append(
                        {
                            "name": name,
                            "description": str(tool.get("description") or "").strip(),
                            "inputSchema": schema,
                            "capability_id": capability_id,
                            "real_tool_name": real_name,
                        }
                    )
        dynamic_tools.sort(key=lambda x: str(x.get("name") or ""))
        dynamic_tool_dropped = 0
        if len(dynamic_tools) > max_dynamic_tools_visible:
            dynamic_tool_dropped = len(dynamic_tools) - max_dynamic_tools_visible
            dynamic_tools = dynamic_tools[:max_dynamic_tools_visible]

        visible_tools = sorted(
            set(resolve_visible_tool_names(builtin_enabled))
            | {str(x.get("name") or "").strip() for x in dynamic_tools if isinstance(x, dict)}
        )

        hidden_capabilities: List[Dict[str, Any]] = []
        for cap_id in all_builtin_pack_ids():
            if cap_id in builtin_enabled:
                continue
            block_entry = blocked_caps.get(cap_id) if isinstance(blocked_caps.get(cap_id), dict) else None
            if isinstance(block_entry, dict):
                scope_token = str(block_entry.get("scope") or "group").strip().lower()
                reason_token = "blocked_by_global_policy" if scope_token == "global" else "blocked_by_group_policy"
                row = {"capability_id": cap_id, "reason": reason_token, "policy_level": "blocked"}
                reason_text = str(block_entry.get("reason") or "").strip()
                if reason_text:
                    row["blocked_reason"] = reason_text
                hidden_capabilities.append(row)
                continue
            level = _effective_policy_level(
                policy,
                capability_id=cap_id,
                kind="mcp_toolpack",
                source_id="cccc_builtin",
                actor_role=actor_role,
            )
            hidden_capabilities.append(
                {
                    "capability_id": cap_id,
                    "reason": "policy_indexed" if (not _policy_level_visible(level)) else "not_enabled",
                    "policy_level": level,
                }
            )

        now = datetime.now(timezone.utc)
        group_enabled_map = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
        actor_enabled_map = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
        session_enabled_map = (
            state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
        )

        per_group_actor = actor_enabled_map.get(group_id) if isinstance(actor_enabled_map.get(group_id), dict) else {}
        per_group_session = (
            session_enabled_map.get(group_id) if isinstance(session_enabled_map.get(group_id), dict) else {}
        )

        def _scope_mismatch_for_capability(capability_id: str) -> bool:
            cap = str(capability_id or "").strip()
            if not cap:
                return False
            if cap in set(group_enabled_map.get(group_id) or []):
                return False
            for aid, items in per_group_actor.items():
                if str(aid) == actor_id:
                    continue
                if cap in set(items if isinstance(items, list) else []):
                    return True
            for aid, entries in per_group_session.items():
                if str(aid) == actor_id or not isinstance(entries, list):
                    continue
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("capability_id") or "").strip() != cap:
                        continue
                    dt = parse_utc_iso(str(item.get("expires_at") or ""))
                    if dt is not None and dt > now:
                        return True
            return False

        session_bindings: List[Dict[str, Any]] = []
        own_session_entries = per_group_session.get(actor_id) if isinstance(per_group_session.get(actor_id), list) else []
        for entry in own_session_entries:
            if not isinstance(entry, dict):
                continue
            cap_id = str(entry.get("capability_id") or "").strip()
            expires_at = str(entry.get("expires_at") or "").strip()
            if not cap_id or not expires_at:
                continue
            dt = parse_utc_iso(expires_at)
            if dt is None:
                continue
            session_bindings.append(
                {
                    "capability_id": cap_id,
                    "expires_at": expires_at,
                    "ttl_seconds": max(0, int((dt - now).total_seconds())),
                }
            )

        with _CATALOG_LOCK:
            catalog_path, catalog_doc = _load_catalog_doc()
            if _ensure_curated_catalog_records(catalog_doc, policy=policy):
                _save_catalog_doc(catalog_path, catalog_doc)
            source_states = _render_source_states(catalog_doc)
            records_raw = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            external_records = {
                str(cid): dict(rec)
                for cid, rec in records_raw.items()
                if isinstance(rec, dict) and str(cid) and (not str(cid).startswith("pack:"))
            }

        actors = group.doc.get("actors") if isinstance(group.doc.get("actors"), list) else []
        actor_record = next(
            (
                a
                for a in actors
                if isinstance(a, dict) and str(a.get("id") or "").strip() == actor_id
            ),
            {},
        )
        actor_autoload_capabilities = _normalize_capability_id_list(
            actor_record.get("capability_autoload") if isinstance(actor_record, dict) else []
        )
        profile_id = str(actor_record.get("profile_id") or "").strip() if isinstance(actor_record, dict) else ""
        profile_autoload_capabilities: List[str] = []
        if profile_id:
            try:
                from ..actors.actor_profile_store import get_actor_profile as _get_actor_profile

                profile_doc = _get_actor_profile(profile_id)
                if isinstance(profile_doc, dict):
                    defaults_cfg = _normalize_profile_capability_defaults(profile_doc.get("capability_defaults"))
                    profile_autoload_capabilities = list(defaults_cfg.get("autoload_capabilities") or [])
            except Exception:
                profile_autoload_capabilities = []
        effective_autoload_capabilities = _normalize_capability_id_list(
            [*profile_autoload_capabilities, *actor_autoload_capabilities]
        )

        active_skills: List[Dict[str, Any]] = []
        for cap_id in enabled_caps_effective:
            rec = external_records.get(cap_id)
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "").strip().lower() != "skill":
                continue
            active_skills.append(
                {
                    "capability_id": cap_id,
                    "name": str(rec.get("name") or cap_id),
                    "description_short": str(rec.get("description_short") or ""),
                    "source_id": str(rec.get("source_id") or ""),
                    "source_uri": str(rec.get("source_uri") or ""),
                    "policy_level": _effective_policy_level(
                        policy,
                        capability_id=cap_id,
                        kind=str(rec.get("kind") or ""),
                        source_id=str(rec.get("source_id") or ""),
                        actor_role=actor_role,
                    ),
                }
            )

        autoload_skills: List[Dict[str, Any]] = []
        for cap_id in effective_autoload_capabilities:
            rec = external_records.get(cap_id)
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "").strip().lower() != "skill":
                continue
            autoload_skills.append(
                {
                    "capability_id": cap_id,
                    "name": str(rec.get("name") or cap_id),
                    "description_short": str(rec.get("description_short") or ""),
                    "source_id": str(rec.get("source_id") or ""),
                    "policy_level": _effective_policy_level(
                        policy,
                        capability_id=cap_id,
                        kind=str(rec.get("kind") or ""),
                        source_id=str(rec.get("source_id") or ""),
                        actor_role=actor_role,
                    ),
                }
            )

        for cap_id, rec in external_records.items():
            block_entry = blocked_caps.get(cap_id) if isinstance(blocked_caps.get(cap_id), dict) else None
            if isinstance(block_entry, dict):
                scope_token = str(block_entry.get("scope") or "group").strip().lower()
                reason_token = "blocked_by_global_policy" if scope_token == "global" else "blocked_by_group_policy"
                row: Dict[str, Any] = {"capability_id": cap_id, "reason": reason_token, "policy_level": "blocked"}
                reason_text = str(block_entry.get("reason") or "").strip()
                if reason_text:
                    row["blocked_reason"] = reason_text
                hidden_capabilities.append(row)
                continue
            policy_level = _effective_policy_level(
                policy,
                capability_id=cap_id,
                kind=str(rec.get("kind") or ""),
                source_id=str(rec.get("source_id") or ""),
                actor_role=actor_role,
            )
            if cap_id in external_enabled:
                state = str(install_state_by_cap.get(cap_id) or "").strip()
                binding = actor_binding_state_by_cap.get(cap_id) if isinstance(actor_binding_state_by_cap.get(cap_id), dict) else {}
                binding_state = str(binding.get("state") or "").strip()
                if not binding_state:
                    hidden_capabilities.append(
                        {
                            "capability_id": cap_id,
                            "reason": "binding_missing",
                            "policy_level": policy_level,
                        }
                    )
                    continue
                if state and (not _install_state_allows_external_tool(state)):
                    install_error = str(install_error_by_cap.get(cap_id) or "").strip()
                    install_error_code = str(install_error_code_by_cap.get(cap_id) or "").strip()
                    hidden_capabilities.append(
                        {
                            "capability_id": cap_id,
                            "reason": "install_failed",
                            "state": state,
                            "policy_level": policy_level,
                            **({"install_error_code": install_error_code} if install_error_code else {}),
                            **({"install_error": install_error} if install_error else {}),
                        }
                    )
                    continue
                if not _binding_state_allows_external_tool(binding_state):
                    hidden_capabilities.append(
                        {
                            "capability_id": cap_id,
                            "reason": "binding_not_ready",
                            "state": binding_state,
                            "policy_level": policy_level,
                        }
                    )
                continue

            if not _policy_level_visible(policy_level):
                hidden_capabilities.append(
                    {
                        "capability_id": cap_id,
                        "reason": "policy_indexed",
                        "policy_level": policy_level,
                    }
                )
                continue

            qualification = str(rec.get("qualification_status") or "").strip().lower()
            if qualification == _QUAL_BLOCKED:
                hidden_capabilities.append(
                    {"capability_id": cap_id, "reason": "policy_blocked", "policy_level": policy_level}
                )
            elif not _record_enable_supported(rec, capability_id=cap_id):
                hidden_capabilities.append(
                    {"capability_id": cap_id, "reason": "unavailable", "policy_level": policy_level}
                )
            elif _scope_mismatch_for_capability(cap_id):
                hidden_capabilities.append(
                    {"capability_id": cap_id, "reason": "scope_mismatch", "policy_level": policy_level}
                )
            else:
                hidden_capabilities.append(
                    {"capability_id": cap_id, "reason": "not_enabled", "policy_level": policy_level}
                )

        external_binding_states: Dict[str, Dict[str, str]] = {}
        for cap_id in external_enabled:
            rec = external_records.get(cap_id)
            kind = str((rec or {}).get("kind") or "").strip().lower() if isinstance(rec, dict) else ""
            install_state = str(install_state_by_cap.get(cap_id) or "").strip()
            binding = actor_binding_state_by_cap.get(cap_id) if isinstance(actor_binding_state_by_cap.get(cap_id), dict) else {}
            entry: Dict[str, str] = {}
            if kind == "skill":
                entry["mode"] = "skill"
                entry["state"] = str(binding.get("state") or "ready_skill")
                if str(binding.get("last_error") or "").strip():
                    entry["last_error"] = str(binding.get("last_error") or "").strip()
            else:
                entry["mode"] = "mcp"
                entry["install_state"] = install_state or "unknown"
                entry["state"] = str(binding.get("state") or (install_state or "unknown"))
                artifact_id = str(binding.get("artifact_id") or install_artifact_by_cap.get(cap_id) or "").strip()
                if artifact_id:
                    entry["artifact_id"] = artifact_id
                install_error = str(install_error_by_cap.get(cap_id) or "").strip()
                install_error_code = str(install_error_code_by_cap.get(cap_id) or "").strip()
                if str(binding.get("last_error") or "").strip():
                    entry["last_error"] = str(binding.get("last_error") or "").strip()
                elif install_state == "installed_degraded":
                    entry["state"] = "ready_degraded"
                    entry["last_error"] = install_error or "probe_timeout"
                    if install_error_code:
                        entry["last_error_code"] = install_error_code
                elif install_state and install_state != "installed":
                    entry["last_error"] = install_error or "install_not_ready"
                    if install_error_code:
                        entry["last_error_code"] = install_error_code
            external_binding_states[cap_id] = entry

        blocked_capabilities: List[Dict[str, str]] = []
        for cap_id in sorted(blocked_caps.keys()):
            entry = blocked_caps.get(cap_id) if isinstance(blocked_caps.get(cap_id), dict) else {}
            item = {
                "capability_id": cap_id,
                "scope": str(entry.get("scope") or "group"),
                "reason": str(entry.get("reason") or ""),
                "by": str(entry.get("by") or ""),
                "blocked_at": str(entry.get("blocked_at") or ""),
                "expires_at": str(entry.get("expires_at") or ""),
            }
            blocked_capabilities.append(item)

        return DaemonResponse(
            ok=True,
            result={
                "group_id": group_id,
                "actor_id": actor_id,
                "default_profile": "core",
                "core_tool_count": len(CORE_TOOL_NAMES),
                "visible_tool_count": len(visible_tools),
                "visible_tools": visible_tools,
                "dynamic_tools": dynamic_tools,
                "dynamic_tool_limit": max_dynamic_tools_visible,
                "dynamic_tool_dropped": dynamic_tool_dropped,
                "enabled_capabilities": enabled_caps_effective,
                "active_skills": active_skills,
                "autoload_skills": autoload_skills,
                "autoload_capabilities": effective_autoload_capabilities,
                "actor_autoload_capabilities": actor_autoload_capabilities,
                "profile_autoload_capabilities": profile_autoload_capabilities,
                "hidden_capabilities": hidden_capabilities,
                "external_binding_states": external_binding_states,
                "precedence_chain": ["session", "actor", "group"],
                "session_bindings": session_bindings,
                "source_states": source_states,
                "blocked_capabilities": blocked_capabilities,
                "is_foreman": bool(_is_foreman(group, actor_id)),
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            return _error("missing_group_id", "missing group_id")
        return _error("capability_state_invalid", message)
    except Exception as e:
        return _error("capability_state_failed", str(e))


def handle_capability_block(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or args.get("actor_id") or "").strip()
    actor_id = str(args.get("actor_id") or by).strip()
    capability_id = str(args.get("capability_id") or "").strip()
    scope = str(args.get("scope") or "group").strip().lower() or "group"
    blocked = bool(args.get("blocked", True))
    ttl_seconds = int(args.get("ttl_seconds") or 0)
    reason = str(args.get("reason") or "").strip()
    if len(reason) > 280:
        reason = reason[:280]
    action_id = f"cblk_{uuid.uuid4().hex[:16]}"

    def _audit(outcome: str, *, state: str = "", error_code: str = "", details: Optional[Dict[str, Any]] = None) -> None:
        payload = dict(details) if isinstance(details, dict) else {}
        if state:
            payload["state"] = state
        if error_code:
            payload["error_code"] = error_code
        if reason:
            payload["reason"] = reason
        try:
            _append_audit_event(
                action_id=action_id,
                op="capability_block",
                group_id=group_id,
                actor_id=actor_id,
                by=by,
                capability_id=capability_id,
                scope=scope,
                enabled=not blocked,
                outcome=outcome,
                details=payload,
            )
        except Exception:
            pass

    try:
        if scope not in {"group", "global"}:
            _audit("failed", state="failed", error_code="invalid_scope")
            return _error("invalid_scope", f"invalid scope: {scope}", details={"action_id": action_id})
        if not capability_id:
            _audit("denied", state="denied", error_code="missing_capability_id")
            return _error("missing_capability_id", "missing capability_id", details={"action_id": action_id})

        group = _ensure_group(group_id) if scope == "group" else None
        if by != "user":
            if not by:
                _audit("denied", state="denied", error_code="missing_actor_id")
                return _error("missing_actor_id", "missing actor identity (by)", details={"action_id": action_id})
            if actor_id != by:
                _audit("denied", state="denied", error_code="permission_denied")
                return _error("permission_denied", "actor can only mutate own block state", details={"action_id": action_id})
            if scope == "global":
                _audit("denied", state="denied", error_code="permission_denied")
                return _error("permission_denied", "only user can mutate global blocklist", details={"action_id": action_id})
            if group is not None and (not _is_foreman(group, by)):
                _audit("denied", state="denied", error_code="permission_denied")
                return _error("permission_denied", "only foreman can mutate group blocklist", details={"action_id": action_id})

        removed_bindings = 0
        block_entry: Dict[str, str] = {}
        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            if blocked:
                block_entry = _set_blocked_capability(
                    state_doc,
                    scope=scope,
                    group_id=group_id,
                    capability_id=capability_id,
                    by=by,
                    reason=reason,
                    ttl_seconds=ttl_seconds,
                )
                if scope == "global":
                    removed_bindings = _remove_capability_bindings_all_groups(state_doc, capability_id=capability_id)
                else:
                    removed_bindings = _remove_capability_bindings(
                        state_doc,
                        group_id=group_id,
                        capability_id=capability_id,
                    )
            else:
                _unset_blocked_capability(
                    state_doc,
                    scope=scope,
                    group_id=group_id,
                    capability_id=capability_id,
                )
            _save_state_doc(state_path, state_doc)

        removed_runtime_bindings = 0
        if blocked:
            with _RUNTIME_LOCK:
                runtime_path, runtime_doc = _load_runtime_doc()
                if scope == "global":
                    removed_runtime_bindings = _remove_runtime_capability_bindings_all_groups(
                        runtime_doc,
                        capability_id=capability_id,
                    )
                else:
                    removed_runtime_bindings = _remove_runtime_group_capability_bindings(
                        runtime_doc,
                        group_id=group_id,
                        capability_id=capability_id,
                    )
                if removed_runtime_bindings > 0:
                    _save_runtime_doc(runtime_path, runtime_doc)

        refresh_required = bool(removed_bindings > 0 or removed_runtime_bindings > 0)
        _audit(
            "ready",
            state="ready",
            details={
                "blocked": bool(blocked),
                "removed_bindings": int(removed_bindings),
                "removed_runtime_bindings": int(removed_runtime_bindings),
            },
        )
        result: Dict[str, Any] = {
            "action_id": action_id,
            "group_id": group_id,
            "actor_id": actor_id,
            "capability_id": capability_id,
            "scope": scope,
            "blocked": bool(blocked),
            "state": "ready",
            "removed_bindings": int(removed_bindings),
            "removed_runtime_bindings": int(removed_runtime_bindings),
            "refresh_required": refresh_required,
        }
        if blocked:
            result["block"] = {
                "reason": str(block_entry.get("reason") or ""),
                "by": str(block_entry.get("by") or by),
                "blocked_at": str(block_entry.get("blocked_at") or utc_now_iso()),
                "expires_at": str(block_entry.get("expires_at") or ""),
            }
        if refresh_required:
            result["wait"] = "relist_or_reconnect"
            result["refresh_mode"] = "relist_or_reconnect"
        return DaemonResponse(ok=True, result=result)
    except LookupError as e:
        _audit("failed", state="failed", error_code="group_not_found", details={"error": str(e)})
        return _error("group_not_found", str(e), details={"action_id": action_id})
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            _audit("failed", state="failed", error_code="missing_group_id")
            return _error("missing_group_id", "missing group_id", details={"action_id": action_id})
        _audit("failed", state="failed", error_code="capability_block_invalid", details={"error": message})
        return _error("capability_block_invalid", message, details={"action_id": action_id})
    except Exception as e:
        _audit("failed", state="failed", error_code="capability_block_failed", details={"error": str(e)})
        return _error("capability_block_failed", str(e), details={"action_id": action_id})


def handle_capability_uninstall(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or args.get("actor_id") or "").strip()
    actor_id = str(args.get("actor_id") or by).strip()
    capability_id = str(args.get("capability_id") or "").strip()
    reason = str(args.get("reason") or "").strip()
    if len(reason) > 280:
        reason = reason[:280]
    action_id = f"cact_{uuid.uuid4().hex[:16]}"

    def _audit(outcome: str, *, state: str = "", error_code: str = "", details: Optional[Dict[str, Any]] = None) -> None:
        payload = dict(details) if isinstance(details, dict) else {}
        if state:
            payload["state"] = state
        if error_code:
            payload["error_code"] = error_code
        if reason:
            payload["reason"] = reason
        try:
            _append_audit_event(
                action_id=action_id,
                op="capability_uninstall",
                group_id=group_id,
                actor_id=actor_id,
                by=by,
                capability_id=capability_id,
                scope="group",
                enabled=False,
                outcome=outcome,
                details=payload,
            )
        except Exception:
            pass

    try:
        group = _ensure_group(group_id)
        if not capability_id:
            _audit("denied", state="denied", error_code="missing_capability_id")
            return _error("missing_capability_id", "missing capability_id", details={"action_id": action_id})
        if by != "user" and (not _is_foreman(group, by)):
            _audit("denied", state="denied", error_code="permission_denied")
            return _error(
                "permission_denied",
                "only user or foreman can uninstall capability",
                details={"action_id": action_id},
            )

        removed_bindings = 0
        has_remaining_binding = False
        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            removed_bindings = _remove_capability_bindings(
                state_doc,
                group_id=group_id,
                capability_id=capability_id,
            )
            has_remaining_binding = _has_any_binding_for_capability(state_doc, capability_id=capability_id)
            if removed_bindings > 0:
                _save_state_doc(state_path, state_doc)

        removed_installation = False
        removed_runtime_bindings = 0
        cleanup_skipped_reason = ""
        with _RUNTIME_LOCK:
            runtime_path, runtime_doc = _load_runtime_doc()
            removed_runtime_bindings = _remove_runtime_group_capability_bindings(
                runtime_doc,
                group_id=group_id,
                capability_id=capability_id,
            )
            runtime_changed = bool(removed_runtime_bindings > 0)
            if has_remaining_binding:
                cleanup_skipped_reason = "cleanup_skipped_capability_still_bound"
            else:
                removed_artifact_id = _remove_runtime_capability_artifact(
                    runtime_doc,
                    capability_id=capability_id,
                )
                if removed_artifact_id:
                    removed_installation = _remove_runtime_artifact_if_unreferenced(
                        runtime_doc,
                        artifact_id=removed_artifact_id,
                    )
                    runtime_changed = True
            if runtime_changed:
                _save_runtime_doc(runtime_path, runtime_doc)

        refresh_required = bool(removed_bindings > 0 or removed_installation or removed_runtime_bindings > 0)
        _audit(
            "ready",
            state="ready",
            details={
                "removed_bindings": int(removed_bindings),
                "removed_installation": bool(removed_installation),
                "removed_runtime_bindings": int(removed_runtime_bindings),
                "cleanup_skipped_reason": cleanup_skipped_reason,
            },
        )
        result: Dict[str, Any] = {
            "action_id": action_id,
            "group_id": group_id,
            "actor_id": actor_id,
            "capability_id": capability_id,
            "state": "ready",
            "removed_bindings": int(removed_bindings),
            "removed_installation": bool(removed_installation),
            "removed_runtime_bindings": int(removed_runtime_bindings),
            "refresh_required": refresh_required,
        }
        if cleanup_skipped_reason:
            result["cleanup_skipped_reason"] = cleanup_skipped_reason
        if refresh_required:
            result["wait"] = "relist_or_reconnect"
            result["refresh_mode"] = "relist_or_reconnect"
        return DaemonResponse(ok=True, result=result)
    except LookupError as e:
        _audit("failed", state="failed", error_code="group_not_found", details={"error": str(e)})
        return _error("group_not_found", str(e), details={"action_id": action_id})
    except ValueError as e:
        message = str(e)
        if message == "missing_group_id":
            _audit("failed", state="failed", error_code="missing_group_id")
            return _error("missing_group_id", "missing group_id", details={"action_id": action_id})
        _audit("failed", state="failed", error_code="capability_uninstall_invalid", details={"error": message})
        return _error("capability_uninstall_invalid", message, details={"action_id": action_id})
    except Exception as e:
        _audit("failed", state="failed", error_code="capability_uninstall_failed", details={"error": str(e)})
        return _error("capability_uninstall_failed", str(e), details={"action_id": action_id})


def handle_capability_tool_call(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or args.get("actor_id") or "").strip()
    actor_id = str(args.get("actor_id") or by).strip()
    capability_id_hint = str(args.get("capability_id") or "").strip()
    tool_name = str(args.get("tool_name") or "").strip()
    arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}

    try:
        group = _ensure_group(group_id)
        if by and by != "user" and actor_id and actor_id != by:
            return _error("permission_denied", "actor can only call tools as self")
        if not actor_id:
            return _error("missing_actor_id", "missing actor_id")
        if not tool_name:
            return _error("missing_tool_name", "missing tool_name")
        if actor_id != "user":
            actors = group.doc.get("actors") if isinstance(group.doc.get("actors"), list) else []
            if actor_id not in {str(a.get("id") or "") for a in actors if isinstance(a, dict)}:
                return _error("actor_not_found", f"actor not found in group: {actor_id}")

        with _STATE_LOCK:
            state_path, state_doc = _load_state_doc()
            enabled_caps, mutated = _collect_enabled_capabilities(state_doc, group_id=group_id, actor_id=actor_id)
            blocked_caps, blocked_mutated = _collect_blocked_capabilities(state_doc, group_id=group_id)
            if mutated or blocked_mutated:
                _save_state_doc(state_path, state_doc)

        enabled_external = [cid for cid in enabled_caps if cid not in BUILTIN_CAPABILITY_PACKS]
        if capability_id_hint and capability_id_hint in BUILTIN_CAPABILITY_PACKS:
            return _error("capability_tool_not_found", f"capability is not external: {capability_id_hint}")
        if capability_id_hint and isinstance(blocked_caps.get(capability_id_hint), dict):
            block_entry = blocked_caps.get(capability_id_hint) if isinstance(blocked_caps.get(capability_id_hint), dict) else {}
            scope_token = str(block_entry.get("scope") or "group").strip().lower()
            reason_code = "blocked_by_global_policy" if scope_token == "global" else "blocked_by_group_policy"
            details = {"capability_id": capability_id_hint, "tool_name": tool_name, "blocked_scope": scope_token}
            reason_text = str(block_entry.get("reason") or "").strip()
            if reason_text:
                details["blocked_reason"] = reason_text
            return _error(reason_code, f"capability blocked: {capability_id_hint}", details=details)
        if capability_id_hint and capability_id_hint not in set(enabled_external):
            return _error(
                "capability_tool_not_found",
                f"capability not enabled for actor: {capability_id_hint}",
                details={"capability_id": capability_id_hint, "tool_name": tool_name},
            )
        candidate_caps = [
            cid
            for cid in ([capability_id_hint] if capability_id_hint else list(enabled_external))
            if not isinstance(blocked_caps.get(cid), dict)
        ]
        if not candidate_caps:
            blocked_ids = [cid for cid in enabled_external if isinstance(blocked_caps.get(cid), dict)]
            if blocked_ids:
                return _error(
                    "blocked_by_group_policy",
                    "all enabled external capabilities are blocked",
                    details={"tool_name": tool_name, "blocked_capabilities": blocked_ids},
                )

        target_capability_id = ""
        target_artifact_id = ""
        target_install: Dict[str, Any] = {}
        real_tool_name = ""
        resolved_tool_name = tool_name
        tool_aliases = set(_tool_name_aliases(tool_name))
        if not tool_aliases:
            tool_aliases = {tool_name}

        with _RUNTIME_LOCK:
            _, runtime_doc = _load_runtime_doc()
            artifacts = _runtime_artifacts(runtime_doc)
            capability_artifacts = _runtime_capability_artifacts(runtime_doc)
            actor_bindings = _runtime_actor_bindings(runtime_doc)
            per_group_bindings = (
                actor_bindings.get(group_id)
                if isinstance(actor_bindings.get(group_id), dict)
                else {}
            )
            per_actor_bindings = (
                per_group_bindings.get(actor_id)
                if isinstance(per_group_bindings.get(actor_id), dict)
                else {}
            )
            matches: List[Tuple[str, str, Dict[str, Any], str, str]] = []
            available_by_cap: Dict[str, set[str]] = {}
            direct_fallback: Optional[Tuple[str, str, Dict[str, Any]]] = None
            for capability_id in candidate_caps:
                binding = per_actor_bindings.get(capability_id) if isinstance(per_actor_bindings, dict) else None
                binding_state = str((binding or {}).get("state") or "").strip() if isinstance(binding, dict) else ""
                if not _binding_state_allows_external_tool(binding_state):
                    continue
                artifact_id = str((binding or {}).get("artifact_id") or "").strip() if isinstance(binding, dict) else ""
                if not artifact_id:
                    artifact_id = str(capability_artifacts.get(capability_id) or "").strip()
                install = artifacts.get(artifact_id) if isinstance(artifacts, dict) else None
                if not isinstance(install, dict):
                    continue
                if not _install_state_allows_external_tool(install.get("state")):
                    continue
                tools = install.get("tools") if isinstance(install.get("tools"), list) else []
                if not tools:
                    if capability_id_hint and capability_id == capability_id_hint:
                        direct_fallback = (capability_id, artifact_id, install)
                    continue
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    synthetic_name = str(tool.get("name") or "").strip()
                    real_name = str(tool.get("real_tool_name") or "").strip()
                    if not synthetic_name or not real_name:
                        continue
                    names = available_by_cap.setdefault(capability_id, set())
                    names.add(synthetic_name)
                    names.add(real_name)
                    if (
                        tool_name != synthetic_name
                        and tool_name != real_name
                        and synthetic_name not in tool_aliases
                        and real_name not in tool_aliases
                    ):
                        continue
                    matches.append((capability_id, artifact_id, install, real_name, synthetic_name))

            if not matches:
                if capability_id_hint and isinstance(direct_fallback, tuple):
                    target_capability_id, target_artifact_id, target_install = direct_fallback
                    real_tool_name = tool_name
                    resolved_tool_name = tool_name
                else:
                    details: Dict[str, Any] = {}
                    if capability_id_hint:
                        available = sorted(available_by_cap.get(capability_id_hint) or [])
                        if available:
                            details["available_tools"] = available[:64]
                        details["capability_id"] = capability_id_hint
                    return _error("capability_tool_not_found", f"tool not found or not enabled: {tool_name}", details=details)
            else:
                if len(matches) > 1:
                    candidates = [
                        {
                            "capability_id": cap_id,
                            "tool_name": synthetic,
                            "real_tool_name": real,
                        }
                        for cap_id, _, _, real, synthetic in matches
                    ]
                    return _error(
                        "capability_tool_ambiguous",
                        f"tool resolves to multiple enabled capabilities: {tool_name}",
                        details={"tool_name": tool_name, "candidates": candidates},
                    )
                target_capability_id, target_artifact_id, target_install, real_tool_name, resolved_tool_name = matches[0]

        try:
            result, resolved_real_tool_name = _invoke_installed_external_tool_with_aliases(
                target_install,
                requested_tool_name=real_tool_name,
                arguments=arguments,
            )
        except Exception as e:
            with _RUNTIME_LOCK:
                # Update last error for observability; do not change enabled scope on transient failures.
                path, doc = _load_runtime_doc()
                artifacts2 = _runtime_artifacts(doc)
                install2 = artifacts2.get(target_artifact_id) if isinstance(artifacts2, dict) else None
                if isinstance(install2, dict):
                    install2["last_error"] = str(e)
                    install2["updated_at"] = utc_now_iso()
                    artifacts2[target_artifact_id] = install2
                    doc["artifacts"] = artifacts2
                    _set_runtime_actor_binding(
                        doc,
                        group_id=group_id,
                        actor_id=actor_id,
                        capability_id=target_capability_id,
                        artifact_id=target_artifact_id,
                        state="tool_call_failed",
                        last_error=str(e),
                    )
                    _save_runtime_doc(path, doc)
            return _error("capability_tool_call_failed", str(e))

        with _RUNTIME_LOCK:
            path, doc = _load_runtime_doc()
            artifacts2 = _runtime_artifacts(doc)
            install2 = artifacts2.get(target_artifact_id) if isinstance(artifacts2, dict) else None
            if isinstance(install2, dict):
                tools2 = install2.get("tools") if isinstance(install2.get("tools"), list) else []
                has_tool = any(
                    isinstance(item, dict)
                    and str(item.get("real_tool_name") or "").strip() == str(resolved_real_tool_name or "").strip()
                    for item in tools2
                )
                if (not has_tool) and str(resolved_real_tool_name or "").strip():
                    used_names = {
                        str(item.get("name") or "").strip()
                        for item in tools2
                        if isinstance(item, dict)
                    }
                    tools2.append(
                        {
                            "name": _build_synthetic_tool_name(
                                target_capability_id,
                                str(resolved_real_tool_name or ""),
                                used=used_names,
                            ),
                            "real_tool_name": str(resolved_real_tool_name or ""),
                            "description": f"{resolved_real_tool_name} tool",
                            "inputSchema": {"type": "object", "properties": {}, "required": []},
                        }
                    )
                install2["tools"] = tools2
                install2["last_error"] = ""
                if str(install2.get("state") or "").strip().lower() == "installed_degraded" and tools2:
                    install2["state"] = "installed"
                install2["updated_at"] = utc_now_iso()
                artifacts2[target_artifact_id] = install2
                doc["artifacts"] = artifacts2
            _set_runtime_actor_binding(
                doc,
                group_id=group_id,
                actor_id=actor_id,
                capability_id=target_capability_id,
                artifact_id=target_artifact_id,
                state="ready",
                last_error="",
            )
            _record_runtime_recent_success(
                doc,
                capability_id=target_capability_id,
                group_id=group_id,
                actor_id=actor_id,
                action="tool_call",
            )
            _save_runtime_doc(path, doc)

        return DaemonResponse(
            ok=True,
            result={
                "tool_name": tool_name,
                "resolved_tool_name": resolved_tool_name,
                "real_tool_name": resolved_real_tool_name,
                "capability_id": target_capability_id,
                "result": result,
            },
        )
    except LookupError as e:
        return _error("group_not_found", str(e))
    except Exception as e:
        return _error("capability_tool_call_failed", str(e))


def try_handle_capability_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    if op == "capability_allowlist_get":
        return handle_capability_allowlist_get(args)
    if op == "capability_allowlist_validate":
        return handle_capability_allowlist_validate(args)
    if op == "capability_allowlist_update":
        return handle_capability_allowlist_update(args)
    if op == "capability_allowlist_reset":
        return handle_capability_allowlist_reset(args)
    if op == "capability_overview":
        return handle_capability_overview(args)
    if op == "capability_search":
        return handle_capability_search(args)
    if op == "capability_enable":
        return handle_capability_enable(args)
    if op == "capability_block":
        return handle_capability_block(args)
    if op == "capability_state":
        return handle_capability_state(args)
    if op == "capability_uninstall":
        return handle_capability_uninstall(args)
    if op == "capability_tool_call":
        return handle_capability_tool_call(args)
    return None

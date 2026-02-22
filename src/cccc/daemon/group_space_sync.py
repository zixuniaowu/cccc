from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..kernel.group import load_group
from ..util.fs import atomic_write_json, read_json
from .group_space_paths import (
    resolve_space_root_from_group,
    resolve_space_root,
    space_index_path,
    space_state_path,
)
from .group_space_provider import (
    SpaceProviderError,
    provider_add_file_source,
    provider_delete_source,
    provider_list_sources,
    provider_rename_source,
)
from .group_space_runtime import acquire_space_provider_write
from .group_space_store import get_space_binding, get_space_provider_state, list_space_bindings

_SYNC_INTERNAL_FILES = {".space-index.json", ".space-sync-state.json", ".space-status.json"}
_SYNC_EXCLUDED_TOP_DIRS = {"artifacts"}
_MARKER_PREFIX = "CCCC::space::"
_PATH_HASH_LEN = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(lo, min(hi, value))


def _reconcile_stale_seconds() -> int:
    return _int_env("CCCC_SPACE_RECONCILE_MAX_STALE_SECONDS", 600, lo=30, hi=86400)


def _path_hash(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:_PATH_HASH_LEN]


def _marker_title(path_hash: str, rel_path: str) -> str:
    base = Path(rel_path).name or "file"
    prefix = f"{_MARKER_PREFIX}{path_hash}::"
    max_title = 180
    available = max(8, max_title - len(prefix))
    if len(base) > available:
        base = base[:available]
    return f"{prefix}{base}"


def _parse_marker_hash(title: Any) -> str:
    text = str(title or "").strip()
    if not text.startswith(_MARKER_PREFIX):
        return ""
    suffix = text[len(_MARKER_PREFIX) :]
    try:
        token, _ = suffix.split("::", 1)
    except ValueError:
        return ""
    token = token.strip().lower()
    if len(token) != _PATH_HASH_LEN:
        return ""
    if any(ch not in "0123456789abcdef" for ch in token):
        return ""
    return token


def _iter_space_files(space_root: Path) -> Iterable[Tuple[Path, str]]:
    try:
        roots = [Path(space_root).resolve()]
    except Exception:
        roots = [space_root]
    if not roots[0].exists():
        return []

    out: List[Tuple[Path, str]] = []
    for current, dirs, files in os.walk(roots[0]):
        current_path = Path(current)
        try:
            rel_current = current_path.relative_to(roots[0])
        except Exception:
            rel_current = Path(".")
        if rel_current == Path("."):
            dirs[:] = sorted(
                [
                    d
                    for d in dirs
                    if (not str(d).startswith(".")) and (str(d) not in _SYNC_EXCLUDED_TOP_DIRS)
                ]
            )
        else:
            dirs[:] = sorted([d for d in dirs if not str(d).startswith(".")])
        for name in sorted(files):
            if name in _SYNC_INTERNAL_FILES:
                continue
            if str(name).startswith("."):
                continue
            abs_path = current_path / name
            if not abs_path.is_file():
                continue
            try:
                rel = abs_path.relative_to(roots[0]).as_posix()
            except Exception:
                continue
            top = rel.split("/", 1)[0]
            if top in _SYNC_EXCLUDED_TOP_DIRS:
                continue
            out.append((abs_path, rel))
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _space_fingerprint(space_root: Path) -> Dict[str, Any]:
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    latest_mtime_ns = 0
    for abs_path, rel_path in _iter_space_files(space_root):
        try:
            st = abs_path.stat()
        except Exception:
            continue
        size = int(st.st_size)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        files += 1
        total_bytes += max(0, size)
        latest_mtime_ns = max(latest_mtime_ns, mtime_ns)
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("utf-8"))
        digest.update(b"\n")
    return {
        "files": files,
        "total_bytes": total_bytes,
        "latest_mtime_ns": latest_mtime_ns,
        "digest": digest.hexdigest(),
    }


def _normalize_index_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    entries_raw = raw.get("entries")
    entries = entries_raw if isinstance(entries_raw, dict) else {}
    norm_entries: Dict[str, Dict[str, Any]] = {}
    for rel_path, item in entries.items():
        rel = str(rel_path or "").strip()
        if not rel:
            continue
        if not isinstance(item, dict):
            continue
        norm_entries[rel] = {
            "rel_path": rel,
            "path_hash": str(item.get("path_hash") or _path_hash(rel)),
            "sha256": str(item.get("sha256") or ""),
            "size": int(item.get("size") or 0),
            "mtime_ns": int(item.get("mtime_ns") or 0),
            "source_id": str(item.get("source_id") or ""),
            "remote_title": str(item.get("remote_title") or ""),
            "last_synced_at": str(item.get("last_synced_at") or ""),
        }
    return {
        "v": 1,
        "group_id": str(raw.get("group_id") or ""),
        "provider": str(raw.get("provider") or "notebooklm"),
        "remote_space_id": str(raw.get("remote_space_id") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "entries": norm_entries,
    }


def _normalize_state_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "v": 1,
        "group_id": str(raw.get("group_id") or ""),
        "provider": str(raw.get("provider") or "notebooklm"),
        "remote_space_id": str(raw.get("remote_space_id") or ""),
        "last_run_at": str(raw.get("last_run_at") or ""),
        "converged": bool(raw.get("converged")),
        "unsynced_count": int(raw.get("unsynced_count") or 0),
        "uploaded": int(raw.get("uploaded") or 0),
        "updated": int(raw.get("updated") or 0),
        "deleted": int(raw.get("deleted") or 0),
        "reused": int(raw.get("reused") or 0),
        "last_error": str(raw.get("last_error") or ""),
        "last_fingerprint": raw.get("last_fingerprint") if isinstance(raw.get("last_fingerprint"), dict) else {},
        "errors": list(raw.get("errors") or []),
    }


def _load_index(space_root: Path) -> Dict[str, Any]:
    return _normalize_index_doc(read_json(space_index_path(space_root)))


def _save_index(space_root: Path, doc: Dict[str, Any]) -> None:
    out = _normalize_index_doc(doc)
    out["updated_at"] = _now_iso()
    atomic_write_json(space_index_path(space_root), out, indent=2)


def _load_state(space_root: Path) -> Dict[str, Any]:
    return _normalize_state_doc(read_json(space_state_path(space_root)))


def _save_state(space_root: Path, doc: Dict[str, Any]) -> None:
    out = _normalize_state_doc(doc)
    atomic_write_json(space_state_path(space_root), out, indent=2)


def _scan_local(space_root: Path, previous_entries: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for abs_path, rel_path in _iter_space_files(space_root):
        try:
            st = abs_path.stat()
        except Exception:
            continue
        size = int(st.st_size)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        prev = previous_entries.get(rel_path) if isinstance(previous_entries.get(rel_path), dict) else {}
        sha = ""
        if prev and int(prev.get("size") or 0) == size and int(prev.get("mtime_ns") or 0) == mtime_ns:
            sha = str(prev.get("sha256") or "").strip()
        if not sha:
            try:
                sha = _file_sha256(abs_path)
            except Exception:
                sha = ""
        out[rel_path] = {
            "rel_path": rel_path,
            "abs_path": str(abs_path),
            "path_hash": _path_hash(rel_path),
            "sha256": sha,
            "size": size,
            "mtime_ns": mtime_ns,
        }
    return out


def _source_id(raw: Dict[str, Any]) -> str:
    return str(raw.get("source_id") or "").strip()


def read_group_space_sync_state(group_id: str) -> Dict[str, Any]:
    space_root = resolve_space_root(group_id, create=False)
    if space_root is None:
        return {"available": False, "reason": "no_local_scope"}
    state = _load_state(space_root)
    state["available"] = True
    state["space_root"] = str(space_root)
    return state


def sync_group_space_files(group_id: str, *, provider: str = "notebooklm", force: bool = False) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    if not gid:
        return {"ok": False, "code": "missing_group_id", "message": "missing group_id"}
    group = load_group(gid)
    if group is None:
        return {"ok": False, "code": "group_not_found", "message": f"group not found: {gid}"}

    binding = get_space_binding(gid, provider=provider)
    if not isinstance(binding, dict):
        return {"ok": False, "code": "space_binding_missing", "message": "group is not bound to provider"}
    if str(binding.get("status") or "") != "bound":
        return {"ok": False, "code": "space_binding_missing", "message": "group space binding is not active"}
    remote_space_id = str(binding.get("remote_space_id") or "").strip()
    if not remote_space_id:
        return {"ok": False, "code": "space_binding_missing", "message": "binding has no remote_space_id"}

    provider_state = get_space_provider_state(provider)
    if not bool(provider_state.get("enabled")) or str(provider_state.get("mode") or "") == "disabled":
        return {"ok": False, "code": "space_provider_disabled", "message": "provider is disabled"}

    space_root = resolve_space_root_from_group(group, create=True)
    if space_root is None:
        return {"ok": False, "code": "no_local_scope", "message": "group has no local scope"}

    index_doc = _load_index(space_root)
    state_doc = _load_state(space_root)

    fingerprint = _space_fingerprint(space_root)
    stale_seconds = _reconcile_stale_seconds()
    last_run = _parse_utc_ts(state_doc.get("last_run_at"))
    age_seconds = None
    if last_run is not None:
        age_seconds = (datetime.now(timezone.utc) - last_run).total_seconds()

    can_skip = (
        (not force)
        and str(state_doc.get("remote_space_id") or "") == remote_space_id
        and bool(state_doc.get("converged"))
        and isinstance(state_doc.get("last_fingerprint"), dict)
        and state_doc.get("last_fingerprint") == fingerprint
        and (age_seconds is not None and age_seconds < float(stale_seconds))
    )
    if can_skip:
        return {
            "ok": True,
            "group_id": gid,
            "provider": provider,
            "remote_space_id": remote_space_id,
            "space_root": str(space_root),
            "skipped": True,
            "reason": "no_local_change",
            "state": state_doc,
        }

    entries = index_doc.get("entries") if isinstance(index_doc.get("entries"), dict) else {}
    local_files = _scan_local(space_root, entries)
    local_by_hash = {str(meta.get("path_hash") or ""): rel for rel, meta in local_files.items()}

    errors: List[Dict[str, str]] = []
    new_entries: Dict[str, Dict[str, Any]] = {}
    keep_source_ids: set[str] = set()
    claimed_remote_ids: set[str] = set()
    deleted_source_ids: set[str] = set()
    uploaded = 0
    updated = 0
    deleted = 0
    reused = 0

    def _append_error(code: str, message: str) -> None:
        errors.append({"code": str(code or "space_upstream_error"), "message": str(message or "provider error")})

    with acquire_space_provider_write(provider, remote_space_id):
        try:
            remote_list = provider_list_sources(provider, remote_space_id=remote_space_id)
        except SpaceProviderError as e:
            err_code = str(e.code or "space_provider_upstream_error")
            message = str(e)
            state_doc.update(
                {
                    "group_id": gid,
                    "provider": provider,
                    "remote_space_id": remote_space_id,
                    "last_run_at": _now_iso(),
                    "converged": False,
                    "unsynced_count": len(local_files),
                    "last_error": message,
                    "last_fingerprint": fingerprint,
                    "errors": [{"code": err_code, "message": message}],
                }
            )
            _save_state(space_root, state_doc)
            return {
                "ok": False,
                "code": err_code,
                "message": message,
                "group_id": gid,
                "provider": provider,
                "remote_space_id": remote_space_id,
                "space_root": str(space_root),
            }

        sources = remote_list.get("sources") if isinstance(remote_list.get("sources"), list) else []
        remote_sources: List[Dict[str, Any]] = [dict(item) for item in sources if isinstance(item, dict)]
        remote_by_id: Dict[str, Dict[str, Any]] = {}
        managed_by_hash: Dict[str, List[Dict[str, Any]]] = {}
        for item in remote_sources:
            sid = _source_id(item)
            if sid:
                remote_by_id[sid] = item
            marker_hash = _parse_marker_hash(item.get("title"))
            if marker_hash:
                managed_by_hash.setdefault(marker_hash, []).append(item)

        for rel_path in sorted(local_files.keys()):
            meta = local_files.get(rel_path) or {}
            prev = entries.get(rel_path) if isinstance(entries.get(rel_path), dict) else {}
            path_hash = str(meta.get("path_hash") or "")
            marker = _marker_title(path_hash, rel_path)
            sid = ""
            source = None
            prev_sid = str(prev.get("source_id") or "").strip()
            if prev_sid and prev_sid in remote_by_id:
                sid = prev_sid
                source = remote_by_id.get(prev_sid)
                claimed_remote_ids.add(prev_sid)
            elif path_hash in managed_by_hash:
                for candidate in managed_by_hash.get(path_hash) or []:
                    cand_sid = _source_id(candidate)
                    if not cand_sid or cand_sid in claimed_remote_ids:
                        continue
                    sid = cand_sid
                    source = candidate
                    claimed_remote_ids.add(cand_sid)
                    break

            same_content = bool(
                source
                and str(prev.get("sha256") or "") == str(meta.get("sha256") or "")
                and str(meta.get("sha256") or "")
            )

            try:
                if same_content and sid:
                    reused += 1
                    if _parse_marker_hash(source.get("title")) != path_hash:
                        renamed = provider_rename_source(
                            provider,
                            remote_space_id=remote_space_id,
                            source_id=sid,
                            new_title=marker,
                        )
                        source["title"] = str(renamed.get("title") or marker)
                    keep_source_ids.add(sid)
                    new_entries[rel_path] = {
                        "rel_path": rel_path,
                        "path_hash": path_hash,
                        "sha256": str(meta.get("sha256") or ""),
                        "size": int(meta.get("size") or 0),
                        "mtime_ns": int(meta.get("mtime_ns") or 0),
                        "source_id": sid,
                        "remote_title": str(source.get("title") or marker),
                        "last_synced_at": _now_iso(),
                    }
                    continue

                add_out = provider_add_file_source(
                    provider,
                    remote_space_id=remote_space_id,
                    file_path=str(meta.get("abs_path") or ""),
                )
                new_sid = _source_id(add_out)
                if not new_sid:
                    raise SpaceProviderError(
                        code="space_provider_upstream_error",
                        message=f"provider add_file returned empty source_id: {rel_path}",
                        transient=False,
                        degrade_provider=False,
                    )
                renamed = provider_rename_source(
                    provider,
                    remote_space_id=remote_space_id,
                    source_id=new_sid,
                    new_title=marker,
                )
                keep_source_ids.add(new_sid)
                remote_title = str(renamed.get("title") or marker)
                if source and sid and sid != new_sid:
                    try:
                        _ = provider_delete_source(
                            provider,
                            remote_space_id=remote_space_id,
                            source_id=sid,
                        )
                        deleted += 1
                        deleted_source_ids.add(sid)
                    except Exception as e:
                        _append_error("space_provider_upstream_error", f"delete old source failed ({rel_path}): {e}")
                if source and sid and sid != new_sid:
                    updated += 1
                else:
                    uploaded += 1
                new_entries[rel_path] = {
                    "rel_path": rel_path,
                    "path_hash": path_hash,
                    "sha256": str(meta.get("sha256") or ""),
                    "size": int(meta.get("size") or 0),
                    "mtime_ns": int(meta.get("mtime_ns") or 0),
                    "source_id": new_sid,
                    "remote_title": remote_title,
                    "last_synced_at": _now_iso(),
                }
            except Exception as e:
                code = "space_provider_upstream_error"
                if isinstance(e, SpaceProviderError):
                    code = str(e.code or code)
                _append_error(code, f"sync failed ({rel_path}): {e}")
                if prev and isinstance(prev, dict):
                    new_entries[rel_path] = dict(prev)

        for rel_path, prev in sorted(entries.items()):
            if rel_path in local_files:
                continue
            prev_sid = str(prev.get("source_id") or "").strip()
            if not prev_sid:
                continue
            if prev_sid not in remote_by_id:
                continue
            try:
                _ = provider_delete_source(
                    provider,
                    remote_space_id=remote_space_id,
                    source_id=prev_sid,
                )
                deleted += 1
                deleted_source_ids.add(prev_sid)
            except Exception as e:
                code = "space_provider_upstream_error"
                if isinstance(e, SpaceProviderError):
                    code = str(e.code or code)
                _append_error(code, f"delete missing-local source failed ({rel_path}): {e}")

        for item in remote_sources:
            sid = _source_id(item)
            if not sid or sid in keep_source_ids or sid in deleted_source_ids:
                continue
            marker_hash = _parse_marker_hash(item.get("title"))
            if not marker_hash:
                continue
            if marker_hash in local_by_hash:
                continue
            try:
                _ = provider_delete_source(
                    provider,
                    remote_space_id=remote_space_id,
                    source_id=sid,
                )
                deleted += 1
                deleted_source_ids.add(sid)
            except Exception as e:
                code = "space_provider_upstream_error"
                if isinstance(e, SpaceProviderError):
                    code = str(e.code or code)
                _append_error(code, f"delete ghost source failed ({sid}): {e}")

    unsynced_count = 0
    for rel_path in local_files.keys():
        item = new_entries.get(rel_path) if isinstance(new_entries.get(rel_path), dict) else {}
        if not str(item.get("source_id") or "").strip():
            unsynced_count += 1
    if errors:
        unsynced_count += len(errors)
    converged = unsynced_count == 0 and len(errors) == 0

    index_doc.update(
        {
            "group_id": gid,
            "provider": provider,
            "remote_space_id": remote_space_id,
            "entries": new_entries,
        }
    )
    _save_index(space_root, index_doc)

    state_doc.update(
        {
            "group_id": gid,
            "provider": provider,
            "remote_space_id": remote_space_id,
            "last_run_at": _now_iso(),
            "converged": converged,
            "unsynced_count": int(unsynced_count),
            "uploaded": int(uploaded),
            "updated": int(updated),
            "deleted": int(deleted),
            "reused": int(reused),
            "last_error": (str(errors[0].get("message") or "") if errors else ""),
            "last_fingerprint": fingerprint,
            "errors": errors[:50],
        }
    )
    _save_state(space_root, state_doc)

    return {
        "ok": True,
        "group_id": gid,
        "provider": provider,
        "remote_space_id": remote_space_id,
        "space_root": str(space_root),
        "skipped": False,
        "converged": converged,
        "unsynced_count": int(unsynced_count),
        "local_files": len(local_files),
        "uploaded": int(uploaded),
        "updated": int(updated),
        "deleted": int(deleted),
        "reused": int(reused),
        "errors": errors[:50],
        "state": state_doc,
    }


def process_due_space_syncs(*, provider: str = "notebooklm", limit: int = 20) -> Dict[str, Any]:
    max_items = max(1, min(int(limit or 20), 200))
    bindings = list_space_bindings(provider)
    seen = 0
    processed = 0
    skipped = 0
    converged = 0
    failed = 0
    for item in bindings:
        if seen >= max_items:
            break
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or "").strip()
        remote_space_id = str(item.get("remote_space_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if not gid or not remote_space_id or status != "bound":
            continue
        seen += 1
        try:
            result = sync_group_space_files(gid, provider=provider, force=False)
            if not bool(result.get("ok")):
                failed += 1
                continue
            if bool(result.get("skipped")):
                skipped += 1
                continue
            processed += 1
            if bool(result.get("converged")):
                converged += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {
        "seen": seen,
        "processed": processed,
        "skipped": skipped,
        "converged": converged,
        "failed": failed,
    }

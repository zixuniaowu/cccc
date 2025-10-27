# -*- coding: utf-8 -*-
from __future__ import annotations
import re, time, hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from .json_util import _read_json_safe, _write_json_safe

def _normalize_signal_text(s: str) -> str:
    try:
        t = re.sub(r"[\x00-\x1F]", " ", s)
        t = re.sub(r"\s+", " ", t)
        return t.strip().lower()
    except Exception:
        return s.strip().lower()

def _tokenize_for_similarity(s: str) -> List[str]:
    try:
        s2 = re.sub(r"[^A-Za-z0-9]+", " ", s or " ")
        toks = [w for w in s2.lower().split() if len(w) >= 2]
        return toks[:8000]
    except Exception:
        return []

def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union

def is_high_signal(text: str, policies: Dict[str,Any]) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    t = (text or '').strip()
    if not t:
        return False
    boosts_k = [k.lower() for k in (cfg.get("boost_keywords_any") or [])]
    boosts_r = cfg.get("boost_regexes") or []
    tl = t.lower()
    if any(k in tl for k in boosts_k):
        return True
    if any(re.search(rx, t, re.I) for rx in boosts_r):
        return True
    if '?' in t:
        return True
    if len(t) >= max(120, int(cfg.get("min_chars", 40)) * 3):
        return True
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) >= max(25, int(cfg.get("min_words", 6)) * 3):
        return True
    return False

def is_low_signal(text: str, policies: Dict[str,Any]) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    if not cfg.get("enabled", True):
        return False
    t = (text or '').strip()
    if not t:
        return True
    if is_high_signal(t, policies):
        return False
    min_chars = int(cfg.get("min_chars", 40))
    min_words = int(cfg.get("min_words", 6))
    words = [w for w in re.split(r"\s+", t) if w]
    is_short = len(t) < min_chars and len(words) < min_words
    if not is_short:
        return False
    drops = cfg.get("drop_regexes") or []
    drop_hit = any(re.search(rx, t, re.I) for rx in drops)
    if not drop_hit:
        return False
    req_k = [k.lower() for k in (cfg.get("require_keywords_any") or [])]
    if req_k:
        tl = t.lower()
        if any(k in tl for k in req_k):
            return False
    return True

def should_forward(payload: str, sender: str, receiver: str, policies: Dict[str,Any], state_dir: Path, override_enabled: Optional[bool]=None) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    enabled = bool(cfg.get("enabled", True)) if override_enabled is None else bool(override_enabled)
    if not enabled:
        return True
    if is_low_signal(payload, policies):
        return False
    key = f"{sender}->{receiver}"
    guard_path = state_dir/"handoff_guard.json"
    guard = _read_json_safe(guard_path)
    now = time.time()
    last = (guard.get(key) or {}).get("last_ts", 0)
    cooldown = float(cfg.get("cooldown_seconds", 15))
    bypass_cool = bool(cfg.get("bypass_cooldown_when_high_signal", True))
    if now - last < cooldown:
        if bypass_cool and is_high_signal(payload, policies):
            pass
        else:
            return False
    dups_path = state_dir/"handoff_dups.json"
    dups = _read_json_safe(dups_path)
    dedup_window = float(cfg.get("dedup_short_seconds", 30.0))
    dedup_keep = int(cfg.get("dedup_max_keep", 10))
    norm = _normalize_signal_text(payload)
    h = hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest()
    items = (dups.get(key) or [])
    items = [it for it in items if now - float(it.get("ts", 0)) <= dedup_window]
    min_chars = int(cfg.get("min_chars", 40)); min_words = int(cfg.get("min_words", 6))
    is_short = len(payload.strip()) < min_chars and len([w for w in re.split(r"\s+", payload.strip()) if w]) < min_words
    if is_short and any(it.get("hash") == h for it in items):
        dups[key] = items
        _write_json_safe(dups_path, dups)
        return False
    items.append({"hash": h, "ts": now}); dups[key] = items[-dedup_keep:]
    _write_json_safe(dups_path, dups)

    red_window = float(cfg.get("redundant_window_seconds", 120.0))
    red_thresh = float(cfg.get("redundant_similarity_threshold", 0.9))
    sim_path = state_dir/"handoff_sim.json"
    sim = _read_json_safe(sim_path)
    sim_items = [it for it in (sim.get(key) or []) if now - float(it.get("ts",0)) <= red_window]
    toks_cur = _tokenize_for_similarity(payload)
    if not is_high_signal(payload, policies):
        for it in sim_items[-5:]:
            simval = _jaccard(toks_cur, it.get("toks", []))
            if simval >= red_thresh:
                sim[key] = sim_items
                _write_json_safe(sim_path, sim)
                return False
    sim_items.append({"ts": now, "toks": toks_cur[:4000]}); sim[key] = sim_items[-dedup_keep:]
    _write_json_safe(sim_path, sim)
    guard[key] = {"last_ts": now}; _write_json_safe(guard_path, guard)
    return True


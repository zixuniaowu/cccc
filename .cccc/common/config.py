# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List
import os, sys

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore


def _read_yaml(p: Path) -> Dict[str, Any]:
    if not p.exists():
        return {}
    try:
        if yaml is not None:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    # extremely small fallback parser (no nested safety)
    out: Dict[str, Any] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split('#', 1)[0].rstrip()
        if not line or ':' not in line:
            continue
        k, v = line.split(':', 1)
        if v.strip():
            out[k.strip()] = v.strip().strip("'\"")
    return out


def _merge_role_and_actor(role: Dict[str, Any], actor_peer: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow merge: role fields (cwd, suffix, boundaries) + actor IO fields for tmux send.
    Role must not override actor IO keys; we enforce at caller.
    """
    merged = dict(role or {})
    for k, v in (actor_peer or {}).items():
        merged[k] = v
    return merged


def _assert_no_role_io_keys(role: Dict[str, Any]):
    forbidden = {
        'input_mode', 'post_paste_keys', 'send_sequence', 'compose_newline_key',
        'idle_quiet_seconds', 'prompt_regex', 'busy_regexes', 'command'
    }
    bad = forbidden.intersection(set((role or {}).keys()))
    if bad:
        raise ValueError(f"Role section contains IO-only keys: {sorted(bad)}. Move them to actors.yaml.")


def load_profiles(home: Path) -> Dict[str, Any]:
    """Read cli_profiles.yaml (roles) and agents.yaml (actors), assemble runtime dict.
    Returns a dict with keys: peerA, peerB, aux, bindings, actors, env_require.
    - peerA/peerB: {actor, cwd, command, profile}
    - aux: {actor, invoke_command, rate_limit_per_minute, cwd}
    - bindings: {'PeerA': actor_id, 'PeerB': actor_id, 'Aux': actor_id}
    - actors: {actor_id: {'capabilities': <str>}}
    - env_require: [names]
    """
    settings = home/"settings"
    roles_yaml = settings/"cli_profiles.yaml"
    actors_yaml = settings/"agents.yaml"
    roles = _read_yaml(roles_yaml)
    actors_doc = _read_yaml(actors_yaml)
    actors = (actors_doc.get('actors') or {}) if isinstance(actors_doc.get('actors'), dict) else {}

    def _role_block(key: str) -> Dict[str, Any]:
        roles_root = roles.get('roles') if isinstance(roles.get('roles'), dict) else roles
        blk = roles_root.get(key) if isinstance(roles_root, dict) else {}
        return blk if isinstance(blk, dict) else {}

    peerA_role = _role_block('peerA')
    peerB_role = _role_block('peerB')
    aux_role   = _role_block('aux')

    for r in (peerA_role, peerB_role, aux_role):
        _assert_no_role_io_keys(r)

    def _resolve_peer(role: Dict[str, Any], fallback_name: str) -> Tuple[str, Dict[str, Any], str]:
        actor_id = str(role.get('actor') or '').strip()
        if not actor_id:
            raise ValueError(f"Missing roles.{fallback_name}.actor")
        ad = actors.get(actor_id)
        if not isinstance(ad, dict):
            raise ValueError(f"Actor '{actor_id}' not found in agents.yaml")
        peer_conf = ad.get('peer') or {}
        if not isinstance(peer_conf, dict):
            raise ValueError(f"Actor '{actor_id}' has no 'peer' section")
        cmd = str(peer_conf.get('command') or '').strip()
        if not cmd:
            raise ValueError(f"Actor '{actor_id}'.peer.command is empty")
        merged_profile = _merge_role_and_actor(role, peer_conf)
        return actor_id, merged_profile, cmd

    pa_actor, pa_profile, pa_cmd = _resolve_peer(peerA_role, 'peerA')
    pb_actor, pb_profile, pb_cmd = _resolve_peer(peerB_role, 'peerB')

    # Aux (new semantics): aux is ON when roles.aux.actor is set; otherwise OFF
    aux_actor_id = str(aux_role.get('actor') or '').strip()
    aux_inv = ''
    rate = aux_role.get('rate_limit_per_minute') or 2
    try:
        rate = int(rate)
    except Exception:
        rate = 2
    if aux_actor_id:
        aux_ad = actors.get(aux_actor_id)
        if not isinstance(aux_ad, dict):
            raise ValueError(f"Actor '{aux_actor_id}' not found in agents.yaml")
        aux_conf = aux_ad.get('aux') or {}
        if not isinstance(aux_conf, dict):
            raise ValueError(f"Actor '{aux_actor_id}' has no 'aux' section")
        aux_inv = str(aux_conf.get('invoke_command') or '').strip()
        if not aux_inv:
            raise ValueError(f"Actor '{aux_actor_id}'.aux.invoke_command is empty")
        # Default rate: role override > actor default
        try:
            rate = int(aux_role.get('rate_limit_per_minute') or aux_conf.get('rate_limit_per_minute') or rate)
        except Exception:
            pass

    # Capabilities for display
    caps: Dict[str, Dict[str, Any]] = {}
    for aid, ad in actors.items():
        cap = ad.get('capabilities')
        caps[aid] = {'capabilities': str(cap or '').strip()}

    # Env requirements
    envs: List[str] = []
    for aid in (pa_actor, pb_actor) + ((aux_actor_id,) if aux_actor_id else tuple()):
        ad = actors.get(aid) or {}
        need = ad.get('env_require') or []
        if isinstance(need, list):
            envs.extend([str(x) for x in need if x])
    envs = sorted(list({e for e in envs if e}))

    return {
        'peerA': {
            'actor': pa_actor,
            'cwd': peerA_role.get('cwd') or '.',
            'command': pa_cmd,
            'profile': pa_profile,
        },
        'peerB': {
            'actor': pb_actor,
            'cwd': peerB_role.get('cwd') or '.',
            'command': pb_cmd,
            'profile': pb_profile,
        },
        'aux': {
            'actor': aux_actor_id,  # empty/None means OFF
            'cwd': aux_role.get('cwd') or None,
            'invoke_command': aux_inv,
            'rate_limit_per_minute': rate,
        },
        'bindings': {'PeerA': pa_actor, 'PeerB': pb_actor, 'Aux': aux_actor_id},
        'actors': caps,
        'env_require': envs,
    }


def ensure_env_vars(keys: List[str], *, prompt: bool = True) -> List[str]:
    """Ensure listed env vars exist in os.environ.
    Returns missing keys (still missing after optional prompt).
    """
    missing = [k for k in keys if not os.environ.get(k)]
    if missing and prompt and sys.stdin.isatty():
        try:
            import getpass  # type: ignore
            for k in list(missing):
                val = getpass.getpass(f"Enter value for {k} (hidden; leave empty to skip): ")
                if val:
                    os.environ[k] = val
        except Exception:
            pass
        missing = [k for k in keys if not os.environ.get(k)]
    return missing

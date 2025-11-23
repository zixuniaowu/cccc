# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, sys, json, time, shlex, subprocess, signal
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List
from .json_util import _read_json_safe, _write_json_safe
from .logging_util import log_ledger

def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']
    settings: Path = ctx['settings']
    state: Path = ctx['state']
    compose_sentinel = ctx['compose_sentinel']
    is_sentinel_text = ctx['is_sentinel_text']
    new_mid = ctx['new_mid']
    read_yaml = ctx['read_yaml']
    write_yaml = ctx.get('write_yaml')
    build_exec_args = ctx.get('build_exec_args')
    load_profiles_fn = ctx.get('load_profiles')
    aux_binding_box = ctx.get('aux_binding_box') or {'template': '', 'cwd': '.'}
    wrap_with_mid = ctx.get('wrap_with_mid')
    write_inbox_message = ctx.get('write_inbox_message')
    sha256_text = ctx.get('sha256_text')
    outbox_write = ctx.get('outbox_write')

    def _foreman_conf_path() -> Path:
        return settings/"foreman.yaml"

    def _load_foreman_conf() -> Dict[str, Any]:
        p = _foreman_conf_path()
        if not p.exists():
            return {"enabled": False, "interval_seconds": 900, "agent": "reuse_aux", "prompt_path": "./FOREMAN_TASK.md", "cc_user": True, "max_run_seconds": 900}
        try:
            import yaml  # type: ignore
            d = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
            d.setdefault("enabled", False); d.setdefault("interval_seconds", 900)
            d.setdefault("agent", "reuse_aux"); d.setdefault("prompt_path", "./FOREMAN_TASK.md")
            d.setdefault("cc_user", True); d.setdefault("max_run_seconds", 900)
            d.setdefault("allowed", d.get("enabled", False))
            return d
        except Exception:
            return {"enabled": False, "interval_seconds": 900, "agent": "reuse_aux", "prompt_path": "./FOREMAN_TASK.md", "cc_user": True, "max_run_seconds": 900, "allowed": False}

    def _save_foreman_conf(conf: Dict[str, Any]):
        try:
            import yaml  # type: ignore
            _foreman_conf_path().parent.mkdir(parents=True, exist_ok=True)
            _foreman_conf_path().write_text(yaml.safe_dump(conf, allow_unicode=True, sort_keys=False), encoding='utf-8')
        except Exception:
            _write_json_safe(_foreman_conf_path(), conf)

    def _foreman_state_path() -> Path:
        return state/"foreman.json"

    def _foreman_load_state() -> Dict[str, Any]:
        return _read_json_safe(_foreman_state_path())

    def _foreman_save_state(st: Dict[str, Any]):
        _write_json_safe(_foreman_state_path(), st)

    def _ensure_foreman_task(conf: Dict[str, Any]):
        try:
            prompt_path = Path(conf.get('prompt_path') or './FOREMAN_TASK.md')
            if not prompt_path.exists():
                tpl = (
"Title: Foreman Task Brief (Project-specific)\n\n"
"Purpose (free text)\n- Describe what matters to the project right now.\n\n"
"Current objectives (ranked, short)\n- 1) \n- 2) \n- 3) \n\n"
"Standing work (edit freely)\n- List repeatable, non-interactive jobs you want Foreman to do from time to time.\n\n"
"Useful references\n- PROJECT.md\n- docs/por/POR.md\n- docs/por/T*/SUBPOR.md\n- docs/evidence/**  and  .cccc/work/**\n\n"
"How to act each run\n- Do one useful, non-interactive step within the time box (≤ 30m).\n- Save temporary outputs to .cccc/work/foreman/<YYYYMMDD-HHMMSS>/.\n- Write one message to .cccc/mailbox/foreman/to_peer.md with header To: Both|PeerA|PeerB and wrap body in <TO_PEER>..</TO_PEER>.\n\n"
"Escalation\n- If a decision is needed, write a 6–10 line RFD and ask the peer.\n\n"
"Safety\n- Do not modify orchestrator code/policies; provide checkable artifacts.\n"
                )
                prompt_path.write_text(tpl, encoding='utf-8')
        except Exception:
            pass

    def _actor_foreman_invoke(actor_id: str) -> str:
        """Get foreman-specific invoke_command from agents.yaml."""
        try:
            actors_doc = read_yaml(settings/"agents.yaml") if read_yaml else {}
            acts = (actors_doc.get('actors') or {}) if isinstance(actors_doc, dict) else {}
            ad = acts.get(actor_id) or {}
            foreman_cfg = ad.get('foreman') or {}
            return str(foreman_cfg.get('invoke_command') or '')
        except Exception:
            return ''

    def _compose_foreman_prompt(conf: Dict[str, Any]) -> Tuple[str, str]:
        """Return (prompt_text, out_dir) where out_dir is a fresh folder for this run."""
        rules_p = home/"rules"/"FOREMAN.md"
        try:
            rules = rules_p.read_text(encoding='utf-8') if rules_p.exists() else ''
        except Exception:
            rules = ''
        bindings: List[str] = []
        try:
            if load_profiles_fn:
                resolved_tmp = load_profiles_fn(home)
            else:
                resolved_tmp = {}
            aux_actor = (resolved_tmp.get('aux') or {}).get('actor') or ''
            bindings.append(f"Bindings: Foreman.agent={conf.get('agent')} Aux={aux_actor or 'none'}")
        except Exception:
            bindings.append(f"Bindings: Foreman.agent={conf.get('agent')}")
        bindings.append(f"Schedule: interval={int(conf.get('interval_seconds',900))}s max_run={int(conf.get('max_run_seconds',900))}s cc_user={'ON' if conf.get('cc_user',True) else 'OFF'}")
        bindings.append("Write-to: .cccc/mailbox/foreman/to_peer.md with To header (Both|PeerA|PeerB; default Both) and <TO_PEER> wrapper")
        ctx_text = "\n".join(bindings)
        task_path = Path(conf.get('prompt_path') or './FOREMAN_TASK.md')
        try:
            task_txt = task_path.read_text(encoding='utf-8') if task_path.exists() else ''
        except Exception:
            task_txt = ''
        out_dir = home/"work"/"foreman"/datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        prompt = f"{rules}\n\n---\n{ctx_text}\n---\n{task_txt}".strip()
        return prompt, out_dir.as_posix()

    def _foreman_write_user_message(to_label: str, body: str):
        try:
            fpath = home/"mailbox"/"foreman"/"to_peer.md"
            fpath.parent.mkdir(parents=True, exist_ok=True)
            if to_label not in ("PeerA","PeerB","Both"):
                to_label = "Both"
            msg = f"To: {to_label}\n" + (body.strip() if body else '')
            fpath.write_text(msg, encoding='utf-8')
        except Exception:
            pass

    def _foreman_write_user_message(to_label: str, body: str):
        try:
            fpath = home/"mailbox"/"foreman"/"to_peer.md"
            hdr = f"To: {to_label}\n" if to_label in ("Both","PeerA","PeerB") else ""
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(hdr + body.strip(), encoding='utf-8')
        except Exception:
            pass

    def _maybe_dispatch_foreman_message():
        try:
            base = home/"mailbox"/"foreman"
            p = base/"to_peer.md"
            if not p.exists():
                return
            raw = p.read_text(encoding='utf-8', errors='replace')
            m = re.search(r"^\s*To\s*:\s*(Both|PeerA|PeerB)\s*$", raw, re.M)
            to_label = m.group(1) if m else 'Both'
            body = raw
            if '<TO_PEER>' not in raw:
                body = f"<TO_PEER>\n{raw.strip()}\n</TO_PEER>\n"
            # emit via peers' inbox
            if to_label in ('Both','PeerA'):
                mid = new_mid("foreman");
                try:
                    from mailbox import compose_sentinel as _c; from mailbox import sha256_text as _sha
                except Exception:
                    _c = compose_sentinel
                payload = f"<FROM_USER>\n{body}\n</FROM_USER>\n"
                try:
                    from delivery import wrap_with_mid as _wrap
                except Exception:
                    _wrap = lambda s,m: s
                # leave actual enqueue to orchestrator main via scan
            # mark as processed
            tsz = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            sentinel = compose_sentinel(ts=tsz, eid=new_mid("foreman")[:8], sha8="", route="Foreman→Peers")
            p.write_text(sentinel, encoding='utf-8')
        except Exception:
            pass

    def _run_foreman_once(conf: Dict[str, Any]):
        prompt, out_dir = _compose_foreman_prompt(conf)
        _ensure_foreman_task(conf)
        agent = str(conf.get('agent') or 'reuse_aux')
        maxs = int(conf.get('max_run_seconds', 900) or 900)
        hb_interval = 10.0
        lock = state/"foreman.lock"
        rc = 1
        argv: List[str] = []
        try:
            try:
                st = _foreman_load_state() or {}
                st.update({'last_start_ts': time.time(), 'running': True, 'last_heartbeat_ts': time.time()})
                _foreman_save_state(st)
                log_ledger(home, {"from":"system","kind":"foreman-start","agent": agent})
            except Exception:
                pass
            if agent == 'reuse_aux':
                # Get aux actor ID and use its foreman config
                try:
                    resolved_tmp = load_profiles_fn(home) if load_profiles_fn else {}
                    aux_actor_id = (resolved_tmp.get('aux') or {}).get('actor') or ''
                except Exception:
                    aux_actor_id = ''
                if aux_actor_id:
                    template = _actor_foreman_invoke(aux_actor_id)
                else:
                    template = ''  # No fallback to aux.invoke_command (which contains AUX.md prefix)
                run_cwd = Path(aux_binding_box.get('cwd') or '.')
            else:
                template = _actor_foreman_invoke(agent)
                run_cwd = Path.cwd()
            if not template:
                log_ledger(home, {"from":"system","kind":"foreman-error","reason":f"actor {agent} has no foreman.invoke_command"})
                return
            if build_exec_args:
                argv = build_exec_args(template, prompt)
            else:
                argv = shlex.split(template) + [prompt]
            out_dir_path = Path(out_dir)
            out_dir_path.mkdir(parents=True, exist_ok=True)
            of = (out_dir_path/"stdout.txt").open('w', encoding='utf-8')
            ef = (out_dir_path/"stderr.txt").open('w', encoding='utf-8')
            try:
                try:
                    preexec = os.setsid
                except Exception:
                    preexec = None
                try:
                    proc = subprocess.Popen(
                        argv,
                        shell=False,
                        cwd=str(run_cwd),
                        stdout=of,
                        stderr=ef,
                        text=True,
                        preexec_fn=preexec,
                    )
                except TypeError:
                    proc = subprocess.Popen(
                        argv,
                        shell=False,
                        cwd=str(run_cwd),
                        stdout=of,
                        stderr=ef,
                        preexec_fn=preexec,
                    )
                try:
                    st2 = _foreman_load_state() or {}
                    st2['pid'] = int(proc.pid)
                    try:
                        st2['pgid'] = int(os.getpgid(proc.pid))
                    except Exception:
                        st2['pgid'] = None
                    _foreman_save_state(st2)
                except Exception:
                    pass
                t0 = time.time(); next_hb = t0 + hb_interval
                while True:
                    rc_local = proc.poll()
                    now = time.time()
                    if rc_local is not None:
                        rc = int(rc_local)
                        break
                    if now - t0 >= maxs:
                        try: proc.terminate()
                        except Exception: pass
                        try: proc.wait(5)
                        except Exception: pass
                        if proc.poll() is None:
                            try: proc.kill()
                            except Exception: pass
                            try: proc.wait(2)
                            except Exception: pass
                        rc = proc.poll() if (proc.poll() is not None) else -9
                        break
                    if now >= next_hb:
                        try:
                            st = _foreman_load_state() or {}
                            st['last_heartbeat_ts'] = now
                            _foreman_save_state(st)
                        except Exception:
                            pass
                        next_hb = now + hb_interval
                    time.sleep(0.5)
            finally:
                try: of.close()
                except Exception: pass
                try: ef.close()
                except Exception: pass
            try:
                _write_json_safe(out_dir_path/"meta.json", {"rc": rc, "agent": agent, "argv": argv})
            except Exception:
                pass
            try:
                f = home/"mailbox"/"foreman"/"to_peer.md"
                cur = f.read_text(encoding='utf-8').strip() if f.exists() else ''
                if (not cur) or is_sentinel_text(cur):
                    so = ''
                    try:
                        with (out_dir_path/"stdout.txt").open('r', encoding='utf-8', errors='replace') as sf:
                            so = sf.read(200000)
                    except Exception:
                        so = ''
                    if re.search(r"^\s*To\s*:\s*(Both|PeerA|PeerB)\s*$", so or "", re.M) and re.search(r"<\s*TO_PEER\s*>", so or "", re.I):
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_text(so.strip(), encoding='utf-8')
            except Exception:
                pass
        finally:
            try:
                st = _foreman_load_state() or {}
                st.update({'last_end_ts': time.time(), 'last_rc': int(rc), 'last_out_dir': out_dir, 'running': False})
                _foreman_save_state(st)
                log_ledger(home, {"from":"system","kind":"foreman-end","rc": int(rc), "out_dir": out_dir})
            except Exception:
                pass
            try:
                if lock.exists():
                    lock.unlink()
            except Exception:
                pass

    def _foreman_stop_running(grace_seconds: float = 5.0) -> None:
        try:
            st = _foreman_load_state() or {}
            pid = int(st.get('pid') or 0)
            pgid = st.get('pgid')
        except Exception:
            pid, pgid = 0, None
        try:
            if pgid:
                try: os.killpg(int(pgid), signal.SIGTERM)
                except Exception: pass
            elif pid:
                try: os.kill(int(pid), signal.SIGTERM)
                except Exception: pass
        except Exception:
            pass
        t_end = time.time() + max(0.0, float(grace_seconds))
        while time.time() < t_end:
            alive = False
            try:
                if pgid:
                    os.killpg(int(pgid), 0); alive = True
                elif pid:
                    os.kill(int(pid), 0); alive = True
            except Exception:
                alive = False
            if not alive:
                break
            time.sleep(0.2)
        try:
            if pgid:
                os.killpg(int(pgid), signal.SIGKILL)
            elif pid:
                os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            st = _foreman_load_state() or {}
            st['running'] = False
            _foreman_save_state(st)
            lk = state/"foreman.lock"
            if lk.exists(): lk.unlink()
        except Exception:
            pass

    return type('FMAPI', (), {
        'load_conf': _load_foreman_conf,
        'save_conf': _save_foreman_conf,
        'state_path': _foreman_state_path,
        'load_state': _foreman_load_state,
        'save_state': _foreman_save_state,
        'ensure_task': _ensure_foreman_task,
        'compose_prompt': _compose_foreman_prompt,
        'write_user_message': _foreman_write_user_message,
        'maybe_dispatch': _maybe_dispatch_foreman_message,
        'run_once': _run_foreman_once,
        'stop_running': _foreman_stop_running,
    })

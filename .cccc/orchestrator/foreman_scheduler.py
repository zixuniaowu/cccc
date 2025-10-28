# -*- coding: utf-8 -*-
from __future__ import annotations
import time, threading
from typing import Dict, Any


def make(ctx: Dict[str, Any]):
    home = ctx['home']
    state = ctx['state']
    log_ledger = ctx['log_ledger']
    load_conf = ctx['load_conf']
    load_state = ctx['load_state']
    save_conf = ctx.get('save_conf')
    save_state = ctx['save_state']
    stop_running = ctx['stop_running']
    run_once = ctx['run_once']
    console_state = ctx['console_state']

    def _next_interval(conf: Dict[str, Any]) -> float:
        try:
            return float(conf.get('interval_seconds', 900) or 900)
        except Exception:
            return 900.0

    def _age(ts: float, now_ts: float) -> str:
        if not ts:
            return "-"
        sec = max(0, int(now_ts - ts))
        return f"{sec}s"

    def tick():
        try:
            fc = load_conf()
            if not bool(fc.get('enabled', False)):
                return
            st = load_state() or {}
            now_ts = time.time()
            lock = state/"foreman.lock"
            foreman_thread = console_state.get('foreman_thread')
            try:
                running = bool(st.get('running', False))
                last_hb = float(st.get('last_heartbeat_ts') or 0.0)
                maxs = float(fc.get('max_run_seconds', 900) or 900)
                if running and ((foreman_thread is None) or (not foreman_thread.is_alive())) and (now_ts - last_hb > (maxs + 20.0)):
                    try:
                        stop_running(grace_seconds=1.0)
                    except Exception:
                        pass
                    st['running'] = False
                    save_state(st)
                    try:
                        if lock.exists():
                            lock.unlink()
                    except Exception:
                        pass
                    log_ledger(home, {"from":"system","kind":"foreman-stale-clean"})
            except Exception:
                pass
            next_due = float(st.get('next_due_ts') or 0.0)
            if next_due <= 0:
                iv = _next_interval(fc)
                st['next_due_ts'] = now_ts + iv
                save_state(st)
                next_due = now_ts + iv
            should_queue = bool(st.get('queued_after_current', False))
            due = now_ts >= float(st.get('next_due_ts') or 0.0)
            foreman_thread = console_state.get('foreman_thread')
            if ((due or should_queue) and (not bool(st.get('running', False))) and ((foreman_thread is None) or (not foreman_thread.is_alive())) and (not lock.exists())):
                st['running'] = True
                st['next_due_ts'] = now_ts + _next_interval(fc)
                if should_queue:
                    st['queued_after_current'] = False
                save_state(st)
                try:
                    lock.write_text(str(int(now_ts)), encoding='utf-8')
                except Exception:
                    pass
                conf_snapshot = dict(fc)
                foreman_thread = threading.Thread(target=run_once, args=(conf_snapshot,), daemon=True)
                foreman_thread.start()
                console_state['foreman_thread'] = foreman_thread
        except Exception as err:
            print(f"[FOREMAN] scheduler error: {err}")

    def command(action: str | None, origin: str = "console") -> Dict[str, Any]:
        label = (action or "status").strip().lower() or "status"
        try:
            fc = load_conf()
        except Exception as err:
            return {"ok": False, "message": f"Foreman config error: {err}"}
        try:
            st = load_state() or {}
        except Exception:
            st = {}
        lock = state/"foreman.lock"
        allowed = bool(fc.get('allowed', fc.get('enabled', False)))
        now_ts = time.time()
        if label in ("on", "enable", "start"):
            if not allowed:
                return {"ok": False, "message": "Foreman was not enabled at startup; restart to enable or run roles wizard."}
            fc['enabled'] = True
            try:
                if save_conf:
                    save_conf(fc)
            except Exception as err:
                return {"ok": False, "message": f"Foreman enable failed: {err}"}
            try:
                st.update({'running': False, 'next_due_ts': now_ts + _next_interval(fc), 'last_heartbeat_ts': now_ts, 'queued_after_current': False})
                save_state(st)
                if lock.exists():
                    try:
                        lock.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
            return {"ok": True, "message": "Foreman enabled"}
        if label in ("now",):
            if not allowed:
                return {"ok": False, "message": "Foreman was not enabled at startup; restart to enable or run roles wizard."}
            running = bool(st.get('running', False))
            if running:
                st['queued_after_current'] = True
                try:
                    save_state(st)
                except Exception:
                    pass
                return {"ok": True, "message": "Foreman already running; queued one run after current finishes."}
            st['next_due_ts'] = now_ts - 1.0
            st['queued_after_current'] = False
            st['running'] = False
            try:
                save_state(st)
            except Exception:
                pass
            try:
                if lock.exists():
                    lock.unlink()
            except Exception:
                pass
            prev_thread = console_state.get('foreman_thread')
            tick()
            new_thread = console_state.get('foreman_thread')
            if new_thread is not None and new_thread is prev_thread:
                msg = "Foreman queued to start (tick scheduled)."
            else:
                msg = "Foreman started (now)"
            return {"ok": True, "message": msg}
        if label in ("off", "disable", "stop"):
            fc['enabled'] = False
            try:
                if save_conf:
                    save_conf(fc)
            except Exception as err:
                return {"ok": False, "message": f"Foreman disable failed: {err}"}
            st['queued_after_current'] = False
            try:
                save_state(st)
            except Exception:
                pass
            return {"ok": True, "message": "Foreman disabled"}
        # status/default
        now = time.time()
        running = bool(st.get('running', False))
        next_due = st.get('next_due_ts') or 0
        next_in = f"{int(max(0, next_due - now))}s" if next_due else "-"
        last_rc = st.get('last_rc')
        last_out = st.get('last_out_dir') or '-'
        summary = (
            f"Foreman status: {'ON' if fc.get('enabled', False) else 'OFF'} allowed={'YES' if allowed else 'NO'} "
            f"agent={fc.get('agent','reuse_aux')} interval={fc.get('interval_seconds','?')}s cc_user={'ON' if fc.get('cc_user',True) else 'OFF'}\n"
            f"running={'YES' if running else 'NO'} next_in={next_in} last_start={_age(float(st.get('last_start_ts') or 0), now)} "
            f"last_hb={_age(float(st.get('last_heartbeat_ts') or 0), now)} last_end={_age(float(st.get('last_end_ts') or 0), now)} "
            f"last_rc={last_rc if last_rc is not None else '-'} out={last_out}"
        )
        return {"ok": True, "message": summary}

    return type('ForemanSchedulerAPI', (), {'tick': tick, 'command': command})

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evidence Runner v0 (generic, offline-friendly)
- Reads an Evidence Card (YAML/JSON) with artifacts[] and checks[]
- Executes checks sequentially with timeout, captures stdout/stderr
- Writes consolidated log under .cccc/work/logs/
- Emits a verdict (pass/fail) + metrics (if parsable) to stdout (JSON)
- Appends a ledger entry to .cccc/state/ledger.jsonl (kind: evidence-validate)

Note: domain-agnostic; no built-in registry. Safe defaults, minimal surface.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple
import os, sys, json, time, subprocess

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # fallback to JSON only

ROOT = Path.cwd()
HOME = ROOT / ".cccc"
WORK_LOGS = HOME / "work" / "logs"
STATE = HOME / "state"
WORK_LOGS.mkdir(parents=True, exist_ok=True)
STATE.mkdir(parents=True, exist_ok=True)

def _read_card(p: Path) -> Dict[str, Any]:
    text = p.read_text(encoding='utf-8')
    if p.suffix.lower() in (".yaml", ".yml") and yaml is not None:
        return yaml.safe_load(text) or {}
    try:
        return json.loads(text)
    except Exception:
        # naive YAML fallback (very limited); encourage YAML install
        data: Dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line and not line.strip().startswith('#'):
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()
        return data

def _run(cmd: str, timeout: int = 120) -> Tuple[int, str, str]:
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); return 124, "", "Timeout"
    return p.returncode, out, err

def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')

def run_checks(card: Dict[str, Any]) -> Dict[str, Any]:
    checks = card.get('checks') or []
    title = card.get('title') or ''
    kind = card.get('kind') or 'generic'
    meta = card.get('meta') or {}
    log_name = f"evrun-{int(time.time())}.log"
    log_path = WORK_LOGS / log_name
    passed_all = True
    check_results: List[Dict[str, Any]] = []

    with log_path.open('w', encoding='utf-8') as lf:
        lf.write(f"[{_now()}] Evidence Runner start kind={kind} title={title}\n")
        lf.write(f"meta: {json.dumps(meta, ensure_ascii=False)}\n")
        lf.write("---\n")
        for c in checks:
            name = str(c.get('name') or 'check')
            cmd  = str(c.get('run') or '')
            tmo  = int(c.get('timeout_sec') or 120)
            allow_fail = bool(c.get('allow_failure') or False)
            lf.write(f"[{_now()}] RUN {name}: {cmd}\n")
            code, out, err = _run(cmd, timeout=tmo)
            lf.write(f"exit={code}\n")
            if out:
                lf.write("[stdout]\n" + out + ("\n" if not out.endswith("\n") else ""))
            if err:
                lf.write("[stderr]\n" + err + ("\n" if not err.endswith("\n") else ""))
            lf.write("---\n")
            ok = (code == 0) or allow_fail
            passed_all = passed_all and ok
            check_results.append({"name": name, "exit": code, "ok": ok})
        lf.write(f"[{_now()}] Evidence Runner end verdict={'pass' if passed_all else 'fail'}\n")

    result = {
        "verdict": "pass" if passed_all else "fail",
        "kind": kind,
        "title": title,
        "checks": check_results,
        "log_path": str(log_path),
        "log_ref": f"LOG:{log_name}#L1-L{sum(1 for _ in open(log_path, 'r', encoding='utf-8'))}",
        "meta": meta,
    }
    return result

def append_ledger(entry: Dict[str, Any]):
    entry = {"ts": _now(), **entry}
    with (STATE/"ledger.jsonl").open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def main(argv: List[str]):
    if len(argv) < 2:
        print("Usage: evidence_runner.py <card.yaml|json>")
        sys.exit(2)
    p = Path(argv[1])
    if not p.exists():
        print(f"Not found: {p}")
        sys.exit(3)
    card = _read_card(p)
    result = run_checks(card)
    append_ledger({
        "kind": "evidence-validate",
        "verdict": result.get('verdict'),
        "title": result.get('title'),
        "evidence_kind": result.get('kind'),
        "log_ref": result.get('log_ref'),
        "checks": result.get('checks'),
        "meta": result.get('meta')
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main(sys.argv)


#!/usr/bin/env python3
# ASCII-only; no external deps.
"""
POR/SUBPOR generator (small tool)

- Templates live under .cccc/settings/templates/{por.md.j2, subpor.md.j2}
- Instances live under docs/por/{POR.md, T######-slug/SUBPOR.md}
-
Usage:
  python .cccc/por_subpor.py por init|sync
  python .cccc/por_subpor.py subpor new --title "My Task" --owner peerB [--slug my-task] [--timebox 1d]
  python .cccc/por_subpor.py subpor open T000123
  python .cccc/por_subpor.py subpor lint T000123

Design:
  - Idempotent; never overwrites existing files.
  - Task IDs are allocated from .cccc/state/seq_task with an exclusive lock.
  - Rendering uses a tiny {{var}} placeholder replacer (no Jinja dependency).
  - Logs actions to .cccc/logs/por_subpor.log.
"""
from __future__ import annotations

import argparse, sys, os, re, json
from pathlib import Path
import datetime as _dt
from datetime import timezone as _tz
import hashlib as _hash

TOOL_VERSION = "0.1.1"


def _cwd() -> Path:
    return Path.cwd()


def _home() -> Path:
    return _cwd() / ".cccc"


def _log_path() -> Path:
    return _home() / "logs" / "por_subpor.log"


def _logs_write(line: str) -> None:
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now(_tz.utc).isoformat(timespec="seconds")
        if not ts.endswith("+00:00"):
            ts += "Z"
        p.open("a", encoding="utf-8").write(f"{ts} {line}\n")
    except Exception:
        pass


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", text.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "task"


def _tmpl_dir() -> Path:
    return _home() / "settings" / "templates"


def _render_template(template_path: Path, subs: dict[str, str]) -> str:
    raw = template_path.read_text(encoding="utf-8")
    if "{{template_sha1}}" in raw:
        subs = dict(subs)
        subs["template_sha1"] = _hash.sha1(raw.encode("utf-8", errors="replace")).hexdigest()
    out = raw
    for k, v in subs.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def _por_path() -> Path:
    return _cwd() / "docs" / "por" / "POR.md"


def _seq_path() -> Path:
    return _home() / "state" / "seq_task"


def _alloc_task_id() -> str:
    p = _seq_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Use advisory lock via fcntl when available; otherwise best-effort
    try:
        import fcntl  # type: ignore
        with p.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            data = f.read().strip()
            n = int(data) if data.isdigit() else 0
            n += 1
            f.seek(0)
            f.truncate()
            f.write(str(n))
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return f"T{n:06d}"
    except Exception:
        # Fallback: race-prone but acceptable in single-user environments
        n = 1
        if p.exists():
            try:
                n = int(p.read_text(encoding="utf-8").strip() or "0") + 1
            except Exception:
                n = 1
        p.write_text(str(n), encoding="utf-8")
        return f"T{n:06d}"


def por_init() -> int:
    por = _por_path()
    if por.exists():
        print(f"POR already exists at {por}")
        return 0
    por.parent.mkdir(parents=True, exist_ok=True)
    tpl = _tmpl_dir() / "por.md.j2"
    _ts = _dt.datetime.now(_tz.utc).isoformat(timespec='seconds')
    header = (f"<!-- Generated on {_ts} by por_subpor.py {TOOL_VERSION} -->\n\n")
    if tpl.exists():
        text = _render_template(tpl, {
            "generated_on": _dt.datetime.now(_tz.utc).isoformat(timespec="seconds"),
            "tool": "por_subpor.py",
            "tool_version": TOOL_VERSION,
        })
    else:
        # Minimal fallback to keep flow unblocked
        text = (
            "# POR · Strategic Board\n"
            "- North Star: <one line>; Guardrails: <quality/safety/cost/latency 2–3 items>\n"
            "- Non-Goals / Boundaries: <1–3 lines>\n\n"
            "## Bets & Assumptions\n- Bet 1: <one intent line> | Probe: <cmd/script> | Evidence: <one line> | Window: <date/threshold>\n\n"
            "## Roadmap (Now/Next/Later)\n- Now (<= 2 weeks): <3–5 lines of intent + criteria>\n\n"
            "## Portfolio Health\n| ID | Title | Owner | Stage | Latest evidence | SUBPOR |\n|----|-------|-------|-------|-----------------|--------|\n\n"
        )
    por.write_text(header + text.strip() + "\n", encoding="utf-8")
    _logs_write(f"por init path={por}")
    print(f"POR created at {por}")
    return 0


def subpor_new(title: str, owner: str, slug: str | None, timebox: str | None, task_id: str | None) -> int:
    if not title.strip():
        print("--title is required", file=sys.stderr)
        return 2
    owner = (owner or "").strip() or "peerB"
    if task_id:
        tid = task_id.strip().upper()
        if not re.match(r"^T\d{6}$", tid):
            print("--id must look like T000123", file=sys.stderr)
            return 2
    else:
        tid = _alloc_task_id()
    slug = _slugify(slug or title)
    base = _cwd() / "docs" / "por" / f"{tid}-{slug}"
    target = base / "SUBPOR.md"
    if target.exists():
        print(f"SUBPOR already exists: {target}")
        return 0
    base.mkdir(parents=True, exist_ok=True)
    tpl = _tmpl_dir() / "subpor.md.j2"
    subs = {
        "id": tid,
        "title": title,
        "owner": owner,
        "stage": "proposed",
        "timebox": (timebox or "1d"),
        "date": _dt.datetime.now(_tz.utc).strftime("%Y-%m-%d"),
        "slug": slug,
    }
    _ts2 = _dt.datetime.now(_tz.utc).isoformat(timespec='seconds')
    header = (f"<!-- Generated on {_ts2} by por_subpor.py {TOOL_VERSION} -->\n\n")
    if tpl.exists():
        text = _render_template(tpl, subs)
    else:
        text = (
            f"# {tid} · {title} · Owner: {owner} · Stage: proposed · Timebox: {subs['timebox']}\n\n"
            "- Goal/Scope (<=3 lines):\n- ...\n"
            "- Non-Goals (<=2 lines):\n- ...\n"
            "- Deliverable & Interface (path/format/user-visible change):\n- ...\n"
            "- Acceptance (3–5 observable items):\n[ ] ...  [ ] ...  [ ] ...\n"
            "- Probe (cheapest decisive): <cmd/script + expected 1–3 stable lines>\n"
            "- Evidence (minimal refs): commit:abc123; cmd:pytest -q::OK; log:.cccc/work/...#L10-22\n"
            "- Risks/Dependencies (1 line each): ...\n"
            "- Next (single, decidable, <=30 minutes): ...\n\n"
            "- Note: Use one line 'Pivot note' only when signals suggest a change of path.\n"
        )
    target.write_text(header + text.strip() + "\n", encoding="utf-8")
    _logs_write(f"subpor new id={tid} path={target}")
    print(f"SUBPOR created at {target}")
    return 0


def subpor_open(task_id: str) -> int:
    tid = task_id.strip().upper()
    if not re.match(r"^T\d{6}$", tid):
        print("task_id must look like T000123", file=sys.stderr)
        return 2
    base_candidates = list((_cwd() / "docs" / "por").glob(f"{tid}-*/SUBPOR.md"))
    if not base_candidates:
        print("not found", file=sys.stderr)
        return 2
    path = base_candidates[0]
    # Best-effort: choose sensible opener
    opener = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not opener:
        print(str(path))
        return 0
    os.system(f"{opener} {path}")
    return 0


def subpor_lint(task_id: str) -> int:
    tid = task_id.strip().upper()
    base_candidates = list((_cwd() / "docs" / "por").glob(f"{tid}-*/SUBPOR.md"))
    if not base_candidates:
        print("not found", file=sys.stderr)
        return 2
    text = base_candidates[0].read_text(encoding="utf-8")
    missing = []
    if "Goal/Scope" not in text:
        missing.append("goal")
    if "Acceptance" not in text:
        missing.append("acceptance")
    if "Probe" not in text:
        missing.append("probe")
    if "Next (single" not in text:
        missing.append("next")
    if "Implementation Approach" not in text:
        missing.append("impl")
    if "## REV" not in text:
        missing.append("rev")
    if missing:
        print("lint: missing sections -> " + ", ".join(missing))
        return 1
    print("lint: ok")
    return 0


def por_sync() -> int:
    p = _por_path()
    if not p.exists():
        print("POR.md not found; run 'por init' first", file=sys.stderr)
        return 2
    text = p.read_text(encoding="utf-8")
    append_blocks: list[str] = []
    def _has(h: str) -> bool:
        return (h in text)
    if not _has("## Deliverables"):
        append_blocks.append("\n## Deliverables (top-level)\n- <deliverable> - path/interface/format - owner\n")
    if not _has("## Decision & Pivot Log"):
        append_blocks.append("\n## Decision & Pivot Log (recent 5)\n- YYYY-MM-DD | context | choice/pivot | evidence | impact/rollback | default\n")
    if not _has("## Risk Radar"):
        append_blocks.append("\n## Risk Radar & Mitigations (up/down/flat)\n- R1: signal/impact/minimal counter (up)\n")
    if not _has("## Operating Principles"):
        append_blocks.append("\n## Operating Principles (short)\n- Falsify before expand; one decidable next step; stop when wrong; Done = evidence.\n")
    if not _has("## Maintenance & Change Log"):
        append_blocks.append("\n## Maintenance & Change Log (append-only, one line each)\n- YYYY-MM-DD HH:MM | who | reason | evidence\n")
    if not append_blocks:
        print("por sync: nothing to add (all sections present)")
        return 0
    p.write_text(text.rstrip("\n") + "\n" + "\n".join(append_blocks) + "\n", encoding="utf-8")
    _logs_write("por sync appended=" + ",".join([b.splitlines()[0] for b in append_blocks]))
    print("por sync: appended sections:")
    for b in append_blocks:
        print("-", b.splitlines()[0].strip())
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="POR/SUBPOR generator")
    sub = ap.add_subparsers(dest="cmd")

    por = sub.add_parser("por", help="POR commands")
    por_sub = por.add_subparsers(dest="subcmd")
    por_sub.add_parser("init", help="Create docs/por/POR.md if missing")
    por_sub.add_parser("sync", help="Append missing high-ROI sections to POR.md (non-destructive)")

    sp = sub.add_parser("subpor", help="SUBPOR commands")
    sp_sub = sp.add_subparsers(dest="subcmd")
    sp_new = sp_sub.add_parser("new", help="Create a new SUBPOR for a task")
    sp_new.add_argument("--title", required=True)
    sp_new.add_argument("--owner", required=True, choices=["peerA", "peerB", "PeerA", "PeerB"])
    sp_new.add_argument("--slug", required=False)
    sp_new.add_argument("--timebox", required=False, default="1d")
    sp_new.add_argument("--id", required=False, help="Override task id (T######), rarely used")
    sp_open = sp_sub.add_parser("open", help="Open an existing SUBPOR")
    sp_open.add_argument("task_id")
    sp_lint = sp_sub.add_parser("lint", help="Check minimal sections are present")
    sp_lint.add_argument("task_id")

    args = ap.parse_args(argv)
    if args.cmd == "por" and args.subcmd == "init":
        return por_init()
    if args.cmd == "por" and args.subcmd == "sync":
        return por_sync()
    if args.cmd == "subpor" and args.subcmd == "new":
        return subpor_new(args.title, args.owner, args.slug, args.timebox, args.id)
    if args.cmd == "subpor" and args.subcmd == "open":
        return subpor_open(args.task_id)
    if args.cmd == "subpor" and args.subcmd == "lint":
        return subpor_lint(args.task_id)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

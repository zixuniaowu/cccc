# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone as _tz, timedelta
import json

def _read_yaml_or_json(p: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

def _rules_dir(home: Path) -> Path:
    d = home / "rules"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _state_dir(home: Path) -> Path:
    d = home / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _calc_rules_hash(home: Path) -> str:
    # Hash a subset of settings that affect rules text
    import hashlib
    parts: list[str] = []
    for name in [
        "settings/cli_profiles.yaml",  # Contains roles (peerA/peerB/aux) config
        "settings/telegram.yaml",
        "settings/slack.yaml",
        "settings/discord.yaml",
        "settings/foreman.yaml",       # Foreman config (separate from cli_profiles)
    ]:
        fp = home / name
        if fp.exists():
            try:
                parts.append(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
    # Bump the generation suffix when changing rules content semantics
    payload = "\n".join(parts) + "\nGEN:10"
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()

def _is_im_enabled(home: Path) -> bool:
    # Consider IM enabled when any configured bridge has a plausible token/config
    def _has_token(d: Dict[str, Any], keys: Tuple[str, ...]) -> bool:
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return True
        return False

    tg = (home/"settings"/"telegram.yaml")
    if tg.exists():
        d = _read_yaml_or_json(tg)
        if _has_token(d, ("token",)):
            return True
        # Some setups only set autostart with environment token
        if bool(d.get("autostart", False)):
            return True
    sl = (home/"settings"/"slack.yaml")
    if sl.exists():
        d = _read_yaml_or_json(sl)
        if _has_token(d, ("app_token", "bot_token")):
            return True
    dc = (home/"settings"/"discord.yaml")
    if dc.exists():
        d = _read_yaml_or_json(dc)
        if _has_token(d, ("bot_token",)):
            return True
    return False

def _read_yaml_or_json_safe(p: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

def _resolve_bindings(home: Path) -> Dict[str, Any]:
    """Return a small dict with current role bindings and aux details.
    Keys: pa, pb, aux_actor, aux_invoke, aux_cwd, aux_rate.
    Missing values become empty strings (actor/invoke) or sensible defaults (cwd='.', rate=2).
    """
    roles_cfg = _read_yaml_or_json_safe(home/"settings"/"cli_profiles.yaml")
    actors_cfg = _read_yaml_or_json_safe(home/"settings"/"agents.yaml")
    roles = roles_cfg.get('roles') if isinstance(roles_cfg.get('roles'), dict) else roles_cfg
    a = roles.get('peerA') if isinstance(roles.get('peerA'), dict) else {}
    b = roles.get('peerB') if isinstance(roles.get('peerB'), dict) else {}
    x = roles.get('aux')   if isinstance(roles.get('aux'), dict)   else {}
    pa = str((a.get('actor') or '')).strip()
    pb = str((b.get('actor') or '')).strip()
    xa = str((x.get('actor') or '')).strip()
    xc = str((x.get('cwd') or '.')).strip() or '.'
    try:
        rate = int(x.get('rate_limit_per_minute') or 2)
    except Exception:
        rate = 2
    aux_invoke = ''
    if xa:
        acts = actors_cfg.get('actors') if isinstance(actors_cfg.get('actors'), dict) else {}
        ad = acts.get(xa) if isinstance(acts.get(xa), dict) else {}
        aux = ad.get('aux') if isinstance(ad.get('aux'), dict) else {}
        aux_invoke = str((aux.get('invoke_command') or '')).strip()
        try:
            rate = int(x.get('rate_limit_per_minute') or aux.get('rate_limit_per_minute') or rate)
        except Exception:
            pass
    return {
        'pa': pa,
        'pb': pb,
        'aux_actor': xa,
        'aux_invoke': aux_invoke,
        'aux_cwd': xc,
        'aux_rate': rate,
    }

def _runtime_bindings_one_liner(home: Path) -> str:
    b = _resolve_bindings(home)
    pa = b['pa'] or '-'
    pb = b['pb'] or '-'
    aux = b['aux_actor'] or 'none'
    line = f"Bindings: PeerA={pa}; PeerB={pb}; Aux={aux}"
    if b['aux_actor'] and b['aux_invoke']:
        # Preserve {prompt} literally
        inv = str(b['aux_invoke']).replace('{prompt}', '{prompt}')
        line += f" invoke=\"{inv}\""
    return line
def _aux_mode(home: Path) -> str:
    """Aux is ON when roles.aux.actor is configured; otherwise OFF."""
    conf_path = home/"settings"/"cli_profiles.yaml"
    conf = _read_yaml_or_json(conf_path) if conf_path.exists() else {}
    roles = conf.get('roles') if isinstance(conf.get('roles'), dict) else {}
    aux_role = roles.get('aux') if isinstance(roles.get('aux'), dict) else {}
    actor = str((aux_role.get('actor') or '').strip())
    return "on" if actor else "off"

def _is_single_peer_mode(home: Path) -> bool:
    """Detect if running in single-peer mode (PeerB=none)."""
    conf_path = home/"settings"/"cli_profiles.yaml"
    conf = _read_yaml_or_json(conf_path) if conf_path.exists() else {}
    roles = conf.get('roles') if isinstance(conf.get('roles'), dict) else conf
    peer_b = roles.get('peerB') if isinstance(roles.get('peerB'), dict) else {}
    actor = str((peer_b.get('actor') or '').strip().lower())
    return not actor or actor == 'none'

def _is_foreman_configured(home: Path) -> bool:
    """Detect if foreman is configured (has an agent assigned).

    Note: This checks if foreman is CONFIGURED, not if it's currently ENABLED.
    - Configured = agent is set (can be toggled on/off at runtime)
    - Not configured = agent is 'none' or missing (foreman unavailable)

    Rule files should exist when foreman is CONFIGURED, regardless of enabled state,
    because user can toggle /foreman on at any time.
    """
    conf_path = home/"settings"/"foreman.yaml"
    conf = _read_yaml_or_json(conf_path) if conf_path.exists() else {}
    agent = str(conf.get('agent', '') or '').strip().lower()
    return bool(agent) and agent != 'none'

def _conversation_reset(home: Path) -> Tuple[str, Optional[int]]:
    profiles = home/"settings"/"cli_profiles.yaml"
    if not profiles.exists():
        return "compact", None
    d = _read_yaml_or_json(profiles)
    delivery = d.get("delivery") if isinstance(d.get("delivery"), dict) else {}
    r = delivery.get("conversation_reset") if isinstance(delivery.get("conversation_reset"), dict) else {}
    policy = str(r.get("policy") or "compact").lower().strip()
    if policy not in ("compact", "clear"):
        policy = "compact"
    try:
        interval = int(r.get("interval_handoffs") or 0)
    except Exception:
        interval = 0
    return policy, (interval if interval > 0 else None)

def _format_local_ts() -> str:
    """Return a human-friendly local timestamp with tz abbrev and UTC offset.
    Example: 2025-09-24 00:17:50 PDT (UTC-07:00)
    """
    dt = datetime.now().astimezone()
    tzname = dt.tzname() or ""
    off = dt.utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    hh = total // 3600
    mm = (total % 3600) // 60
    offset_str = f"UTC{sign}{hh:02d}:{mm:02d}"
    main = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{main} {tzname} ({offset_str})" if tzname else f"{main} ({offset_str})"

def _write_rules_for_peer(home: Path, peer: str, *, im_enabled: bool, aux_mode: str, single_peer_mode: bool = False) -> Path:
    is_peera = (peer.lower() == "peera" or peer == "peerA")
    role_name = "PeerA" if is_peera else "PeerB"
    rules_filename = "PEERA.md" if is_peera else "PEERB.md"
    base = f".cccc/mailbox/{peer}"
    to_user = f"{base}/to_user.md"
    to_peer = f"{base}/to_peer.md"
    aux_enabled = aux_mode == "on"

    # Single-peer mode: only PeerA is active
    if single_peer_mode:
        ch1 = [
            "1) Who You Are - Mode - Purpose",
            "- Single-peer mode",
            "  - You are the sole executing agent with full CCCC infrastructure: Foreman, Aux, self-check, auto-compact, Blueprint, IM bridges.",
            "  - User provides direction and decisions; System maintains work rhythm via keepalive.",
            "- Ethos (non-negotiable)",
            "  - Agency and ownership; act like a top generalist.",
            "  - Widen perspective; Keep higher-order thinking.",
            "  - Evidence-first; chat never changes state.",
            "  - Taste and clarity: simple, tight, clean.",
            "  - Anti-laziness: refuse low-signal output; prefer decisive micro-moves.",
            "- Self-review emphasis (important in single-peer mode)",
            "  - Without a peer to review your work: after significant steps, ask yourself \"Is this direction correct?\"",
            "  - Before committing changes: \"What could go wrong?\"",
            "  - When uncertain: Report to user via to_user.md rather than proceed blindly.",
        ]
    else:
        ch1 = [
            "1) Who You Are - Collaborators - Purpose",
            "- Equal peers",
            "  - You and the other peer collaborate as equals to deliver evidence-first, small, reversible steps that outperform a single expert.",
            "- Ethos (non-negotiable)",
            "  - Agency and ownership; act like a top generalist.",
            "  - Widen perspective; Keep higher-order thinking.",
            "  - Evidence-first; chat never changes state.",
            "  - Taste and clarity: simple, tight, clean.",
            "  - Anti-laziness: refuse low-signal output; prefer decisive micro-moves.",

        ]
    if aux_enabled:
        ch1 += [
            "- On-demand helper: Aux - purpose & direction",
            "  - Use Aux when a decoupled subtask or high-level sanity sweep is cheaper offloaded than done inline. You integrate the outcome.",
            "  - Mode: on - Aux has the same FoV and permissions in this repo as you. Just call Aux for help.",
        ]
    else:
        aux_desc = "handle strategy checks and heavy lifting directly." if not single_peer_mode else "handle strategy checks and heavy lifting yourself."
        ch1 += [
            "- Aux availability",
            f"  - Aux is disabled for this run. You {aux_desc}",
        ]

    # PeerB must not address USER directly. Make the I/O boundary explicit early.
    if not is_peera and not single_peer_mode:
        ch1 += [
            "- IO contract (strict)",
            "  - Outbound routes: to_peer only. Never send to USER. All user-facing messages are owned by PeerA or System.",
        ]

    ch2 = [
        "",
        "2) Canonical references and anchors",
        "- context/ directory - execution status tracking (ccontext compatible)",
        "  - context/context.yaml: milestones (project phases), notes (lessons), references",
        "  - context/tasks/T###.yaml: active task definitions with steps",
        "  - Use milestones to track project journey; use tasks for concrete work items",
        "- Ownership (CCCC policy)",
        "  - PeerA creates/deletes milestones and tasks; maintains the tree.",
        "  - PeerB does NOT create/delete milestones/tasks; flag gaps to PeerA.",
        "  - Both peers update vision/sketch/milestone/task content.",
        "- Task system - structured task tracking for complex work",
        "  - Location: context/tasks/T###.yaml",
        "  - Use for: multi-step goals (>2 files OR >50 lines); work spanning multiple handoffs; user-requested planning",
        "  - Skip for: quick tasks (<=2 files AND <=50 lines); immediate fixes; simple questions",
        "  - See Chapter 3 for full protocol",
        "- Work surfaces",
        "  - Use .cccc/work/** for scratch, samples, logs. Cite exact paths and line ranges instead of pasting large blobs.",
        "  - Boundary: do not modify orchestrator code/config/policies; use mailbox/work/state/logs exactly as documented.",
        "- PROJECT.md - user-facing scope and context (repo root, maintained by user)",
        "  - Read to align on vision, constraints, stakeholders, non-goals, and links. Do NOT edit unless explicitly asked by the user.",
    ]

    ch3 = [
        "",
        "3) How to execute (lean and decisive)",
        "- One-round loop (follow in order)",
        "  - Align before you act; keep one decidable next step per message (<=30 minutes).",
        "  - 0 Check context: scan context/context.yaml for milestones and context/tasks/T*.yaml for active tasks.",
        "  - 1 If no active milestone, create/activate one in context/context.yaml first.",
        "  - 2 Pick exactly one smallest decisional probe.",
        "  - 3 Build; keep changes small and reversible.",
        "  - 4 Validate (command + 1-3 stable lines; cite exact paths/line ranges).",
        "  - 5 Write the message using the skeleton in Chapter 4.",
        "  - 6 Add one insight (WHY + Next + refs); do not repeat the body.",
        "  - 7 Update task status; record lessons in context/context.yaml notes if learned something important.",
        "- Evidence and change budget",
        "  - Done = has verifiable evidence (commit/test/log).",
        "  - Only tests/logs/commits count as evidence. Avoid speculative big refactors; always show the smallest reproducible check.",
        "- Pivot and refusal (signals and judgment; not quotas)",
        "  - Pivot when two or more hold: negative evidence piles up; a simpler alternative is clearly smaller or safer; infra cost exceeds benefit; guardrails are repeatedly hit; roadmap Now/Next has shifted.",
        "  - Refuse and rebuild: when foundations are bad or artifact quality is low, refuse review and propose the smallest from-scratch probe instead of patching a mess.",
        "- NUDGE behavior (one-liner)",
        "  - On [NUDGE]: read oldest inbox item -> act -> move to processed/ -> next; reply only when blocked.",
        "- Message protocol (per item, machine-readable lines)",
        "  - Write by items: `Item(<label>): <title>` (label = free text like lint.preflight; not a task id).",
        "  - Inside an item, use at most these event lines (≤3 per item recommended):",
        "    Progress[(tag=...)]: <real progress only>",
        "    Evidence[(tag=..., refs=[commit:...,cmd:...,log:...])]: <1–3 stable lines>",
        "    Ask[(to=peerA|peerB, tag=..., prio=high|normal, action?=review|clarify|revise)]: <question/request>",
        "    Counter[(tag=..., strength=high|normal)]: <the strongest opposite you propose to act on>",
        "    Risk[(tag=..., sev=high|med|low)]: <specific risk>",
        "    Next[(tag=...)]: <single next step, ≤30 minutes>",
        "  - Human-only lines (not parsed): Outcome / Why / Opposite / Files / Refs.",
        "  - Non-English aliases are supported at runtime; documentation uses English keywords only.",
        "- Next keepalive",
        "  - When you include a `Next:` line declaring your next step, System sends a continuation prompt (~60s) to maintain work rhythm if you stall.",
    ]
    # Single-peer mode: simplified channel guidance (use to_user.md primarily)
    if single_peer_mode:
        ch3 += [
            "- Communication channel (single-peer)",
            "  - Use **to_user.md** for all output - progress, questions, and results.",
            "  - Include Next markers to declare your next step and trigger keepalive.",
        ]
    # PeerB should not create milestones/tasks; rewrite step 1 accordingly
    if (not is_peera) and (not single_peer_mode):
        ch3 = [
            ("  - 1 If no active milestone, ask PeerA to create/activate one in context/context.yaml."
             if line.startswith("  - 1 If no active milestone") else line)
            for line in ch3
        ]
    # Aux section reflects current binding (actor/invoke/cwd/rate)
    from pathlib import Path as _P
    bnd = _resolve_bindings(home)
    if aux_enabled and bnd.get('aux_actor'):
        actor = bnd.get('aux_actor') or '-'
        inv = (bnd.get('aux_invoke') or '').replace('{prompt}', '{prompt}')
        cwd = bnd.get('aux_cwd') or './'
        rate = bnd.get('aux_rate') or 2
        ch3 += [
            "- Aux (this run) {#aux}",
            f"  - Actor: {actor}; cwd: {cwd}; rate: {rate}/min",
            f"  - Invoke (template): {inv if inv else '-'}",
            "  - Delegate decoupled sub-tasks; you remain reviewer/integrator and own final evidence.",
        ]
    else:
        ch3 += [
            "- Aux (this run) {#aux}",
            "  - Aux is disabled (no actor bound). Use the startup roles wizard to bind Aux when needed.",
        ]

    # PeerB: correct path when user input is required.
    if not is_peera:
        ch3 += [
            "- When you need USER input (PeerB)",
            "  - Write an Ask(to=peerA, action=relay_to_user, ...) line under the relevant Item. Do not address USER directly.",
        ]

    # Context System - unified collaboration state
    # Design principle: Why → What → How (concept first, operation second)
    ch3_blueprint = [
        "- Context System {#context}",
        "  PURPOSE: Shared state enabling collaboration visibility, progress tracking, and coordination.",
        "",
        "  CONCEPT HIERARCHY (understand before operating):",
        "    ┌─ Vision ────── What we're building (one-liner, stable)",
        "    ├─ Sketch ────── Execution blueprint: current phase, priorities, risks (update on phase changes)",
        "    ├─ Milestones ── Project phases with checkpoints (M1, M2...)",
        "    ├─ Tasks ─────── Concrete work items with steps (T001, T002... in context/tasks/)",
        "    ├─ Presence ──── Live agent status: who's doing what NOW (auto + manual updates)",
        "    └─ Notes/Refs ── Accumulated knowledge and references",
        "",
        "  FILES:",
        "    context/context.yaml  → vision, sketch, milestones, notes, references",
        "    context/tasks/T###.yaml → individual task definitions with steps",
        "    context/presence.yaml → agent status (gitignored, runtime state)",
        "",
    ]

    # Role-specific structure creation rules (milestones + tasks)
    if single_peer_mode:
        ch3_blueprint += [
            "  MILESTONE/TASK CREATION: You create and manage milestones and tasks",
            "    • Milestones: create/activate in context/context.yaml (or MCP: create_milestone)",
            "    • Create T###.yaml BEFORE complex work (>=3 files OR >50 lines OR multi-step)",
            "    • Quick work: execute directly, create task mid-work if complexity grows",
            "",
        ]
    elif is_peera:
        ch3_blueprint += [
            "  MILESTONE/TASK CREATION [PeerA responsibility]:",
            "    • You CREATE milestones and T###.yaml files (PeerB updates existing, cannot create new)",
            "    • Milestones: create/activate in context/context.yaml (or MCP: create_milestone)",
            "    • Threshold: >=3 files OR >50 lines OR multi-step work",
            "    • Create BEFORE coding to prevent ID conflicts",
            "    • Quick work: execute directly, create task mid-work if complexity grows",
            "",
        ]
    else:
        ch3_blueprint += [
            "  MILESTONE/TASK CREATION [PeerB role]:",
            "    • Do NOT create milestones or T###.yaml (PeerA creates to avoid duplicates/ID conflicts)",
            "    • You CAN update existing tasks: steps, status, sub-steps",
            "    • You CAN update existing milestones/tasks content (status/desc/outcomes/steps) when aligned",
            "    • If an active milestone/task is missing, ask PeerA to create/activate it",
            "",
        ]

    # Operation methods - simplified, no verbose schemas
    ch3_blueprint += ["  HOW TO OPERATE:", "", "    MCP Tools (preferred when available):"]
    ch3_blueprint += [
        "      Read:      get_context, get_presence, list_tasks",
        "      Blueprint: update_vision, update_sketch",
    ]
    if single_peer_mode or is_peera:
        ch3_blueprint += [
            "      Structure: create_milestone, create_task",
            "      Progress:  update_milestone, update_task, commit_updates",
        ]
    else:
        ch3_blueprint += [
            "      Progress:  update_milestone, update_task, commit_updates",
            "      Create:    (not allowed) ask PeerA to create milestones/tasks",
        ]
    ch3_blueprint += [
        "      Status:    update_my_status, clear_status",
        "      Knowledge: add_note, update_note, add_reference, update_reference",
        "",
        "    Direct YAML (fallback or when MCP unavailable):",
        "      Edit context/context.yaml for vision, sketch, milestones, notes, refs",
        "      Edit context/tasks/T###.yaml for task details and step status",
        "      Edit context/presence.yaml to update your status manually",
        "      (Refer to existing files for schema - they are self-documenting)",
        "",
        "  PRESENCE PROTOCOL:",
        f"    • Your agent_id: {'peer-a' if is_peera else 'peer-b'} (use this exact value in update_my_status)",
        "    • Presence = natural language description of what you're doing/thinking (1-2 sentences)",
        "    • Example: 'Debugging JWT edge case, found timezone issue' or 'Waiting for PeerA to confirm schema'",
        "    • Update when: starting work, making progress, hitting blockers, completing work",
        "    • Your peer and user see this in TUI header - keep it meaningful and current",
        "",
    ]
    ch3 += ch3_blueprint

    update_targets = [to_peer]
    if is_peera:
        update_targets.insert(0, to_user)
    target_list = ", ".join(update_targets)

    ch4 = [
        "",
        "4) Communicate (message skeleton and file I/O)",
        "- Writing rules (strict)",
        f"  - Update-only: always overwrite {target_list}; do NOT append or create new variants.",
        "  - After sending, your message file is replaced with a one-line status sentinel 'MAILBOX:SENT v1 …'. Don't care about it; simply overwrite the whole file with your next message.",
        "  - Encoding: UTF-8 (no BOM).",
        "  - Do not claim done unless you include minimal verifiable evidence (tests/stable logs/commit refs). If using tasks, update task status.",
    ]
    ch4 += [
        "  - Keep <TO_USER>/<TO_PEER> wrappers; end with exactly one fenced `insight` block (insight is for explore/reflect/idea only; the system does not parse governance from it).",
        "  - Do not modify orchestrator code/config/policies.",
        "- Message skeleton (ready to copy) {#message-skeleton}",
        "  <TO_PEER>",
        "  Item(<label>): <title>",
        "  Outcome: <one-line conclusion> ; Why: <one-line reason> ; Opposite: <one-line strongest opposite>",
        "  Progress: <only when there is real progress>",
        "  Evidence(refs=[commit:...,cmd:...::OK,log:...#L..-L..]): <<=3 stable lines>",
        "  Ask(to=peerA|peerB, action=review|clarify|revise): <decidable question>",
        "  Counter(strength=high|normal): <formal counter you want acted on>",
        "  Risk(sev=high|med|low): <specific risk>",
        "  Next: <single, decidable, <=30 minutes>",
        "",
        "  Item(<optional second label>): <title>",
        "  Outcome: <...>",
        "  Evidence: <...>",
        "  Risk: <...>",
        "",
        "  Files: <paths#line-slices>",
        "  </TO_PEER>",
        "  ```insight",
        "  explore: <idea or hypothesis>",
        "  reflect: <short meta reflection>",
        "  ```",
        "- Consolidated EVIDENCE (end-of-execution; single message)",
        "  - Changes: files=N, +X/-Y; key paths: [...]",
        "  - What changed and why: <one line>",
        "  - Checks: <cmd + stable 1-2 lines> -> pass|fail|n/a",
        "  - Risks/unknowns: [...]",
        "  - Next: <one smallest decisive step>",
        f"  - refs: [\"context/context.yaml\", \".cccc/rules/{role_name}.md#...\"]",
        "- File I/O (keep these two lines verbatim) {#file-io}",
        "  - Inbound: uploads go to .cccc/work/upload/inbound/YYYYMMDD/MID__name with a sibling .meta.json; also indexed into state/inbound-index.jsonl.",
        "  - Outbound: drop files into .cccc/work/upload/outbound/ (flat). Use <name>.route with a|b|both or first line of <name>.caption.txt starting with a:/b:/both:. On success a <name>.sent.json ACK is written.",
        "- Channel notes (minimal)",
        "  - Peer-to-peer: high signal; one smallest Next per message; steelman before COUNTER; silence is better than a pure ACK.",
        "  - User-facing (when used): <=12 lines; conclusion first, then evidence paths; questions must be decidable.",
    ]
    if im_enabled:
        route_prefix = "a" if is_peera else "b"
        ch4 += [
            "- IM routing & passthrough (active) {#im}",
            "  - Chat routing: `a:`, `b:`, `both:` or `/a`, `/b`, `/both` from IM land in your mailbox; process them like any other inbox item.",
            f"  - Direct CLI passthrough: `{route_prefix}! <command>` runs inside your CLI pane; capture outputs in .cccc/work/** when they matter.",
            "  - System commands such as /focus, /reset, /aux-cli, /review from IM arrive as <FROM_SYSTEM> notes; act and report in your next turn.",
        ]

    ts = _format_local_ts()
    text = "\n".join([
        f"# {role_name} Rules (Generated)",
        f"Generated on {ts}",
        "",
        *ch1, *ch2, *ch3, *ch4,
        "",
    ])
    target = _rules_dir(home)/rules_filename
    target.write_text(text, encoding="utf-8")
    return target


def _write_rules_for_aux(home: Path, *, aux_mode: str) -> Path:
    session_root = (home/"work"/"aux_sessions").as_posix()

    ch1 = [
        "1) Role & Activation",
        "- You are Aux, an on-demand specialist. PeerA/PeerB summon you for focused subtasks.",
        f"- Session bundle: {session_root}/<session-id>/ (notes.txt, peer_message.txt, artifacts)",
        "- One session = one deliverable. Keep moves small, testable, reversible.",
    ]
    bnd = _resolve_bindings(home)
    if bnd.get('aux_actor'):
        ch1.append(f"- Binding: actor={bnd.get('aux_actor')}; cwd={bnd.get('aux_cwd') or './'}; rate={bnd.get('aux_rate') or 2}/min")

    ch2 = [
        "",
        "2) Context (read in order)",
        "- notes.txt: the ask, expectations, success criteria",
        "- peer_message.txt: triggering message context (if present)",
        "- context/context.yaml: milestones and execution status",
        "- context/tasks/T*.yaml: active tasks (do NOT create new tasks; update existing if instructed)",
        "- PROJECT.md: project vision and constraints",
    ]

    ch3 = [
        "",
        "3) Execution",
        "- Plan: break into <=15-minute probes; deterministic scripts over exploration",
        "- Build: all scratch in session dir; capture commands + 3-5 log lines in <session-id>/logs/",
        "- Wrap: outcome.md (what changed, checks run, risks, next suggestion)",
    ]

    ch4 = [
        "",
        "4) Boundaries",
        "- Never edit .cccc/mailbox/** - summoning peer integrates your output",
        "- Do NOT create new T###.yaml files - only update existing tasks if instructed",
        "- Reference paths instead of pasting large outputs",
        "- Document strategic misalignments in outcome.md with correction path",
    ]

    ts = _format_local_ts()
    text = "\n".join([
        "# Aux Rules (Generated)",
        f"Generated on {ts}",
        "",
        *ch1, *ch2, *ch3, *ch4,
        "",
    ])
    target = _rules_dir(home)/"AUX.md"
    target.write_text(text, encoding="utf-8")
    return target

def ensure_rules_docs(home: Path):
    """Ensure rules docs exist and reflect current config hash.
    - Behavior: generate when missing OR when the computed hash changes.
    - Rationale: cheap, safe, idempotent guard to keep rules reasonably fresh
      without forcing disk writes on every call.
    """
    # Generate rules if missing or when config hash changed
    h = _calc_rules_hash(home)
    state = _state_dir(home)
    stamp = state/"rules_hash.json"
    old = {}
    try:
        old = json.loads(stamp.read_text(encoding="utf-8"))
    except Exception:
        old = {}

    # Detect current configuration
    single_peer = _is_single_peer_mode(home)
    aux_mode = _aux_mode(home)
    foreman_configured = _is_foreman_configured(home)

    # Check required files exist (only check files for ACTIVE components)
    peera_missing = not (home/"rules"/"PEERA.md").exists()
    peerb_missing = not single_peer and not (home/"rules"/"PEERB.md").exists()
    aux_missing = (aux_mode == "on") and not (home/"rules"/"AUX.md").exists()
    foreman_missing = foreman_configured and not (home/"rules"/"FOREMAN.md").exists()
    hash_changed = old.get("hash") != h

    if peera_missing or peerb_missing or aux_missing or foreman_missing or hash_changed:
        im_enabled = _is_im_enabled(home)

        # Generate PEERA.md (always needed)
        _write_rules_for_peer(home, "peerA", im_enabled=im_enabled, aux_mode=aux_mode, single_peer_mode=single_peer)

        # PEERB.md: generate only in dual-peer mode, delete in single-peer mode
        if not single_peer:
            _write_rules_for_peer(home, "peerB", im_enabled=im_enabled, aux_mode=aux_mode, single_peer_mode=False)
        else:
            peerb_rules = home / "rules" / "PEERB.md"
            if peerb_rules.exists():
                try:
                    peerb_rules.unlink()
                except Exception:
                    pass

        # AUX.md: generate only when aux is enabled, delete when disabled
        if aux_mode == "on":
            _write_rules_for_aux(home, aux_mode=aux_mode)
        else:
            aux_rules = home / "rules" / "AUX.md"
            if aux_rules.exists():
                try:
                    aux_rules.unlink()
                except Exception:
                    pass

        # FOREMAN.md: generate only when foreman is configured, delete when not configured
        if foreman_configured:
            _write_rules_for_foreman(home)
        else:
            foreman_rules = home / "rules" / "FOREMAN.md"
            if foreman_rules.exists():
                try:
                    foreman_rules.unlink()
                except Exception:
                    pass

        try:
            stamp.write_text(json.dumps({"hash": h}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

def rebuild_rules_docs(home: Path):
    """Rebuild rules docs unconditionally (called at launch time after TUI confirms settings).
    - Generates rule files only for ACTIVE/CONFIGURED components:
      - PEERA.md: always generated
      - PEERB.md: only in dual-peer mode (deleted in single-peer mode)
      - AUX.md: only when aux is enabled (deleted when disabled)
      - FOREMAN.md: only when foreman is configured (deleted when not configured)
        Note: "configured" means agent is set (not 'none'), regardless of enabled state
    - Updates state/rules_hash.json to the current computed hash so that
      subsequent ensure_rules_docs() calls are no-ops for this run unless
      settings change.
    """
    im_enabled = _is_im_enabled(home)
    aux_mode = _aux_mode(home)
    single_peer = _is_single_peer_mode(home)
    foreman_configured = _is_foreman_configured(home)

    # Generate PEERA.md (always needed)
    _write_rules_for_peer(home, "peerA", im_enabled=im_enabled, aux_mode=aux_mode, single_peer_mode=single_peer)

    # PEERB.md: generate only in dual-peer mode, delete in single-peer mode
    if not single_peer:
        _write_rules_for_peer(home, "peerB", im_enabled=im_enabled, aux_mode=aux_mode, single_peer_mode=False)
    else:
        peerb_rules = home / "rules" / "PEERB.md"
        if peerb_rules.exists():
            try:
                peerb_rules.unlink()
            except Exception:
                pass

    # AUX.md: generate only when aux is enabled, delete when disabled
    if aux_mode == "on":
        _write_rules_for_aux(home, aux_mode=aux_mode)
    else:
        aux_rules = home / "rules" / "AUX.md"
        if aux_rules.exists():
            try:
                aux_rules.unlink()
            except Exception:
                pass

    # FOREMAN.md: generate only when foreman is configured, delete when not configured
    if foreman_configured:
        _write_rules_for_foreman(home)
    else:
        foreman_rules = home / "rules" / "FOREMAN.md"
        if foreman_rules.exists():
            try:
                foreman_rules.unlink()
            except Exception:
                pass
    # Record hash so later 'ensure' calls can skip
    try:
        h = _calc_rules_hash(home)
        state = _state_dir(home)
        (state/"rules_hash.json").write_text(json.dumps({"hash": h}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _ensure_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def weave_minimal_system_prompt(home: Path, peer: str, por: Optional[Dict[str, Any]] = None) -> str:
    """Minimal SYSTEM entry point (currently reuses the full system prompt).
    We keep this function as a clear, future-friendly seam; at present it
    returns the exact same text as weave_system_prompt to avoid diverging
    sources of truth.
    """
    return weave_system_prompt(home, peer, por)

# ---------- Foreman rules (system prompt; generated; not user‑edited) ----------
def _write_rules_for_foreman(home: Path) -> Path:
    ts = _format_local_ts()
    lines = [
        "# FOREMAN Rules (Generated)",
        f"Generated on {ts}",
        "",
        "Identity",
        "- You are the user's autonomous proxy. Speak in the user's voice.",
        "- Each run: time-boxed, non-interactive. Do one useful thing per run.",
        "",
        "Output",
        "- Write to: `.cccc/mailbox/foreman/to_peer.md`",
        "- Header: `To: Both|PeerA|PeerB` (default Both)",
        "- Body: `<TO_PEER> ... </TO_PEER>`",
        "",
        "Context (skim then decide)",
        "- PROJECT.md: vision/constraints",
        "- context/context.yaml: milestones and execution status",
        "- context/tasks/T*.yaml: active tasks and progress",
        "- .cccc/mailbox/*/inbox: pending items",
        "",
        "Task awareness",
        "- Check active tasks before suggesting new work",
        "- If tasks exist, propose steps that align with current task goals",
        "- Remind peers of stale tasks (no progress for extended time)",
        "",
        "Routing",
        "- Architecture/alignment/risks → PeerA",
        "- Implementation/experiments → PeerB",
        "- Pending inbox items: remind to process oldest-first",
        "",
        "Boundaries",
        "- Reference paths, not paste content",
        "- Do not modify orchestrator code/policies",
        "- Do not create new T###.yaml files",
        "",
    ]
    target = _rules_dir(home)/"FOREMAN.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def weave_system_prompt(home: Path, peer: str, por: Optional[Dict[str, Any]] = None) -> str:
    """Full SYSTEM: rules document for the target peer (+ one-line bindings).
    This is the single source we maintain; callers that want a minimal
    variant should call weave_minimal_system_prompt (currently identical).
    """
    peer = (peer or "peerA").strip()
    try:
        ensure_rules_docs(home)
    except Exception:
        pass
    try:
        rules_file = (home/"rules"/("PEERA.md" if (peer.lower()=="peera" or peer=="peerA") else "PEERB.md"))
        if rules_file.exists():
            txt = rules_file.read_text(encoding="utf-8")

            # Inject PROJECT.md at startup (same format as Kth self-check refresh)
            # This ensures peers have full context (vision/constraints/non-goals) from the start
            try:
                proj_path = Path.cwd() / "PROJECT.md"
                if proj_path.exists():
                    proj_txt = proj_path.read_text(encoding='utf-8', errors='replace')
                    # Prepend PROJECT.md before SYSTEM rules (matching self-check format)
                    txt = f"--- PROJECT.md (full) ---\n{proj_txt}\n\n--- SYSTEM (full) ---\n{txt}"
            except Exception:
                pass  # Silent fallback: continue with rules only if PROJECT.md unavailable

            # Append runtime bindings one-liner for this session
            try:
                txt = txt.rstrip() + "\n\n" + _runtime_bindings_one_liner(home) + "\n"
            except Exception:
                pass

            # Append presence/sketch section for real-time awareness
            try:
                presence_sketch = _presence_sketch_section(home)
                if presence_sketch:
                    txt = txt.rstrip() + "\n\n" + presence_sketch
            except Exception:
                pass

            return txt
    except Exception:
        pass
    # Fallback: construct a minimal banner if rules are not yet available
    rules_path = (home/"rules"/("PEERA.md" if (peer.lower()=="peera" or peer=="peerA") else "PEERB.md")).as_posix()
    other = "peerB" if (peer.lower()=="peera" or peer=="peerA") else "peerA"
    lines = [
        "CCCC Runtime SYSTEM (full)",
        f"* You are {peer}. Collaborate as equals with {other}.",
        f"* Context: context/context.yaml (milestones); context/tasks/ (tasks).",
        f"* Rules: {rules_path} - follow this document; keep <TO_USER>/<TO_PEER> wrappers; end with exactly one fenced insight block.",
        "",
    ]
    try:
        lines.append(_runtime_bindings_one_liner(home))
        lines.append("")
    except Exception:
        pass
    # Add presence/sketch section in fallback as well
    try:
        presence_sketch = _presence_sketch_section(home)
        if presence_sketch:
            lines.append(presence_sketch)
            lines.append("")
    except Exception:
        pass
    return "\n".join(lines)


def _presence_sketch_section(home: Path) -> str:
    """
    Generate presence and sketch section for system prompts.

    This provides agents with real-time awareness of:
    - Other agents' current status and tasks (Presence)
    - The high-level execution blueprint (Sketch)

    Args:
        home: Path to .cccc directory

    Returns:
        Formatted string with presence and sketch data
    """
    lines: list[str] = []

    # Derive project root from home (.cccc directory)
    project_root = home.parent if home.name == '.cccc' else home

    # Read context.yaml for vision and sketch
    context_path = project_root / "context" / "context.yaml"
    context: Dict[str, Any] = {}
    if context_path.exists():
        try:
            context = _read_yaml_or_json(context_path)
        except Exception:
            pass

    # Vision (brief)
    vision = context.get('vision')
    if vision:
        vision_str = str(vision).strip()
        if vision_str:
            lines.append("--- Vision ---")
            lines.append(vision_str)
            lines.append("")

    # Sketch (execution blueprint)
    sketch = context.get('sketch')
    if sketch:
        sketch_str = str(sketch).strip()
        if sketch_str:
            lines.append("--- Execution Blueprint (Sketch) ---")
            lines.append("NOTE: Sketch is a static blueprint only (no TODO/progress/tasks here).")
            lines.append(sketch_str)
            lines.append("")

    # Now (execution status summary): active milestone + active tasks
    try:
        milestones_raw = context.get('milestones', [])
        milestones = milestones_raw if isinstance(milestones_raw, list) else []
        active_ms: Optional[Dict[str, Any]] = None
        for m in milestones:
            if isinstance(m, dict) and str(m.get('status', '')).lower() == 'active':
                active_ms = m
                break

        tasks_dir = project_root / "context" / "tasks"
        active_tasks: list[str] = []
        if tasks_dir.exists():
            for fp in sorted(tasks_dir.glob("T*.yaml")):
                try:
                    tdata = _read_yaml_or_json(fp)
                except Exception:
                    continue
                if not isinstance(tdata, dict):
                    continue
                st = str(tdata.get('status', '')).lower()
                if st == 'active':
                    tid = str(tdata.get('id') or fp.stem).strip()
                    name = str(tdata.get('name') or '').strip()
                    if name:
                        if len(name) > 40:
                            name = name[:37] + "..."
                        active_tasks.append(f"{tid} {name}".strip())
                    else:
                        active_tasks.append(tid)

        if active_ms or active_tasks or milestones or tasks_dir.exists():
            lines.append("--- Now (Execution Status) ---")
            if active_ms:
                ms_id = str(active_ms.get('id') or '').strip()
                ms_name = str(active_ms.get('name') or '').strip()
                ms_line = f"{ms_id} {ms_name}".strip() or ms_id or ms_name or "(unnamed)"
                lines.append(f"Active Milestone: {ms_line}")
            else:
                lines.append("Active Milestone: (none)")
            if active_tasks:
                shown = active_tasks[:5]
                lines.append("Active Tasks: " + "; ".join(shown))
                if len(active_tasks) > 5:
                    lines.append(f"... and {len(active_tasks)-5} more")
            else:
                lines.append("Active Tasks: (none)")
            lines.append("")
    except Exception:
        pass

    # Presence (agent status)
    presence_path = project_root / "context" / "presence.yaml"
    if presence_path.exists():
        try:
            presence_data = _read_yaml_or_json(presence_path)
            agents = presence_data.get('agents', [])
            if agents:
                lines.append("--- Agent Presence (Live Status) ---")
                for agent in agents:
                    agent_id = agent.get('id', 'unknown')
                    status = agent.get('status', '')
                    updated_at = agent.get('updated_at', '')

                    # Status display
                    if status:
                        icon = '●'
                        status_display = status
                    else:
                        icon = '○'
                        status_display = '(idle)'

                    parts = [f"{icon} {agent_id}: {status_display}"]
                    if updated_at:
                        parts.append(f"@{updated_at[:19]}")

                    lines.append("  " + " | ".join(parts))
                lines.append("")
        except Exception:
            pass

    return "\n".join(lines)


def _runtime_bindings_snippet(home: Path) -> str:
    def _read_yaml(p: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore
            return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        except Exception:
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                return {}
    roles = _read_yaml(home/"settings"/"cli_profiles.yaml")
    actors_doc = _read_yaml(home/"settings"/"agents.yaml")
    actors = actors_doc.get('actors') or {}
    def _role(key: str) -> Dict[str, Any]:
        root = roles.get('roles') or roles
        blk = root.get(key) or {}
        return blk if isinstance(blk, dict) else {}
    a = _role('peerA'); b = _role('peerB'); x = _role('aux')
    def _cap(aid: str) -> str:
        ad = actors.get(aid) or {}
        cap = ad.get('capabilities')
        return str(cap or '').strip()
    def _aux_inv(aid: str) -> str:
        ad = actors.get(aid) or {}
        aux = ad.get('aux') or {}
        inv = aux.get('invoke_command') or ''
        s = str(inv or '').strip()
        # mask braces to avoid accidental template expansion by models
        return s.replace('{prompt}', "{prompt}")
    pa = str(a.get('actor') or '').strip(); pb = str(b.get('actor') or '').strip(); px = str(x.get('actor') or '').strip()
    sc = [
        "Runtime Bindings (this run)",
        f"- PeerA: actor={pa or '-'}, cwd={a.get('cwd') or './'}, capabilities: {_cap(pa) or '-'}",
        f"- PeerB: actor={pb or '-'}, cwd={b.get('cwd') or './'}, capabilities: {_cap(pb) or '-'}",
        f"- Aux:   actor={px or 'none'}, invoke=\"{_aux_inv(px)}\""
    ]
    return "\n".join(sc)

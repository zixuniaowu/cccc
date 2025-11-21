# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone as _tz, timedelta

from por_manager import ensure_por, por_path, ensure_aux_section
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
        "settings/cli_profiles.yaml",
        "settings/telegram.yaml",
        "settings/slack.yaml",
        "settings/discord.yaml",
    ]:
        fp = home / name
        if fp.exists():
            try:
                parts.append(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
    # Bump the generation suffix when changing rules content semantics
    payload = "\n".join(parts) + "\nGEN:9"
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

def _write_rules_for_peer(home: Path, peer: str, *, im_enabled: bool, aux_mode: str) -> Path:
    is_peera = (peer.lower() == "peera" or peer == "peerA")
    role_name = "PeerA" if is_peera else "PeerB"
    rules_filename = "PEERA.md" if is_peera else "PEERB.md"
    base = f".cccc/mailbox/{peer}"
    to_user = f"{base}/to_user.md"
    to_peer = f"{base}/to_peer.md"
    por_rel = por_path(home).as_posix()
    aux_enabled = aux_mode == "on"

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
        ch1 += [
            "- Aux availability",
            "  - Aux is disabled for this run. You and your peer handle strategy checks and heavy lifting directly.",
        ]

    # PeerB must not address USER directly. Make the I/O boundary explicit early.
    if not is_peera:
        ch1 += [
            "- IO contract (strict)",
            "  - Outbound routes: to_peer only. Never send to USER. All user-facing messages are owned by PeerA or System.",
        ]

    ch2 = [
        "",
        "2) Canonical references and anchors",
        f"- POR.md - single source of direction (path: {por_rel})",
        "  - Keep North-star, guardrails, bets/assumptions, Now/Next/Later, and portfolio health here (no details).",
        "- SUBPOR - execution anchor (one task = one SUBPOR)",
        "  - Location: docs/por/T######-slug/SUBPOR.md",
        "  - Sections: goal/scope; non-goals; deliverable and interface; 3-5 acceptance items; cheapest probe; evidence refs; risks/deps; next (single, decidable).",
        "  - Rule: Do NOT create a new SUBPOR unless the other peer explicitly ACKs your propose-subtask.",
        ("  - SUBPOR creation is owned only by PeerB. Both peers can update/maintain the sheet after creation." if is_peera else "  - SUBPOR creation is owned only by YOU. Both peers can update/maintain the sheet after creation."),
        ("" if is_peera else "  - Create after ACK: python .cccc/por_subpor.py subpor new --title \"...\" --owner peerB [--slug s] [--timebox 1d]"),
        "- Work surfaces",
        "  - Use .cccc/work/** for scratch, samples, logs. Cite exact paths and line ranges instead of pasting large blobs.",
        "  - Boundary: do not modify orchestrator code/config/policies; use mailbox/work/state/logs exactly as documented.",
        "- PROJECT.md - user-facing scope and context (repo root, maintained by user)",
        "  - Read to align on vision, constraints, stakeholders, non-goals, and links. Do NOT edit unless explicitly asked by the user.",
        "  - If PROJECT.md and POR drift, note a one-line clarification in POR and continue with the updated direction; propose edits to the user via <TO_USER> if needed.",
    ]

    ch3 = [
        "",
        "3) How to execute (lean and decisive)",
        "- One-round loop (follow in order)",
        "  - Align before you act; keep one decidable next step per message (<=30 minutes).",
        "  - 0 Read POR (goal/guardrails/bets/roadmap).",
        "  - 1 Pick exactly one smallest decisional probe.",
        "  - 2 Build; keep changes small and reversible.",
        "  - 3 Validate (command + 1-3 stable lines; cite exact paths/line ranges).",
        "  - 4 Write the message using the skeleton in Chapter 4.",
        "  - 5 Add one insight (WHY + Next + refs); do not repeat the body.",
        "  - 6 If direction changed, update POR and the relevant SUBPOR.",
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
        "- Progress keepalive (runtime)",
        "  - When your message contains a `Progress:` line and the other peer stays silent, the orchestrator may send you a delayed (~60s, configurable) FROM_SYSTEM keepalive to continue your next step; it is suppressed when your inbox already has messages or there are in‑flight/queued handoffs for you.",
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
        "  - Do not claim done unless acceptance is checked in SUBPOR and you include minimal verifiable evidence (tests/stable logs/commit refs).",
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
        f"  - refs: [\"POR.md#...\", \".cccc/rules/{role_name}.md#...\"]",
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
    por_rel = por_path(home).as_posix()
    session_root = (home/"work"/"aux_sessions").as_posix()

    ch1 = [
        "1) Role - Activation - Expectations",
        "- You are Aux, the on-demand third peer. PeerA/PeerB summon you for strategic corrections and heavy execution that stay reversible.",
        "- Activation: orchestrator drops a bundle under .cccc/work/aux_sessions/<session-id>/ containing POR.md, notes.txt, peer_message.txt, and any extra context.",
        "- Rhythm: operate with the same evidence-first standards as the primary peers - small, testable moves and explicit next checks.",
    ]
    bnd = _resolve_bindings(home)
    if bnd.get('aux_actor'):
        # For Aux's own rulebook, show binding state (who I am, cwd, rate),
        # but do NOT include the caller-side invoke template. That template
        # belongs in PeerA/PeerB docs — Aux does not need to know how peers
        # invoke it.
        ch1.append(f"- Binding: actor={bnd.get('aux_actor')}; cwd={bnd.get('aux_cwd') or './'}; rate={bnd.get('aux_rate') or 2}/min")
    else:
        ch1.append("- Binding: none (Aux disabled). Bind an Aux actor at startup via the roles wizard to enable offloads.")

    ch2 = [
        "",
        "2) Critical References & Inputs",
        "- PROJECT.md - project introduction and task description. Read this first to understand the project context and current objectives.",
        f"- POR.md - single source of direction (path: {por_rel}). Always reconcile the bundle against the latest POR before proposing actions.",
        f"- Session bundle - {session_root}/<session-id>/",
        "  - Read notes.txt first: it captures the ask, expectations, and any suggested commands.",
        "  - peer_message.txt (when present) mirrors the triggering CLAIM/COUNTER/EVIDENCE; use it to align tone and scope.",
        "  - Additional artifacts (logs, datasets) live alongside; cite exact paths in your outputs.",
        "- This rules document - .cccc/rules/AUX.md. Reference anchors from here in any summary you produce for the peers.",
    ]

    ch3 = [
        "",
        "3) Execution Cadence",
        "- Intake",
        "  - Read POR.md -> notes.txt -> peer_message.txt. Confirm the objective, constraints, and success criteria before editing.",
        "- Plan",
        "  - Break work into <=15-minute probes. Prefer deterministic scripts or tight analyses over sprawling exploration.",
        "- Build",
        "  - Use .cccc/work/aux_sessions/<session-id>/ for all scratch files, analysis notebooks, and outputs.",
        "  - Run validations as you go. Capture exact commands and 3-5 stable log lines in `<session-id>/logs/`.",
        "- Wrap",
        "  - Summarize the outcome in `<session-id>/outcome.md` (what changed, checks performed, residual risks, next suggestion).",
        "  - Highlight any assumptions that still need falsification so the invoking peer can follow up.",
    ]

    ch4 = [
        "",
        "4) Deliverables & Boundaries",
        "- Never edit .cccc/mailbox/** directly; the summoning peer integrates your artifacts into their next message.",
        "- Keep changes small and reversible. If you create multiple options, name them clearly (e.g., option-a, option-b).",
        "- Record every check you run (command + stable output) so peers can cite them as evidence.",
        "- If you uncover strategic misalignment, document it succinctly in outcome.md with a proposed correction path keyed to POR.md sections.",
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
    if (not (home/"rules"/"PEERA.md").exists()) or (not (home/"rules"/"PEERB.md").exists()) or (not (home/"rules"/"AUX.md").exists()) or (not (home/"rules"/"FOREMAN.md").exists()) or (old.get("hash") != h):
        ensure_por(home)  # make sure POR exists for path rendering
        im_enabled = _is_im_enabled(home)
        aux_mode = _aux_mode(home)
        _write_rules_for_peer(home, "peerA", im_enabled=im_enabled, aux_mode=aux_mode)
        _write_rules_for_peer(home, "peerB", im_enabled=im_enabled, aux_mode=aux_mode)
        _write_rules_for_aux(home, aux_mode=aux_mode)
        _write_rules_for_foreman(home)
        # Append Aux section in POR only when Aux is enabled and the section does not exist yet
        if aux_mode == "on":
            try:
                ensure_aux_section(home)
            except Exception:
                pass
        try:
            stamp.write_text(json.dumps({"hash": h}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

def rebuild_rules_docs(home: Path):
    """Rebuild rules docs unconditionally (used once at orchestrator startup).
    - Always rewrites .cccc/rules/PEERA.md, PEERB.md, AUX.md with fresh
      timestamps and current IM/Aux mode derived from settings/env.
    - Updates state/rules_hash.json to the current computed hash so that
      subsequent ensure_rules_docs() calls are no-ops for this run unless
      settings change.
    """
    ensure_por(home)
    im_enabled = _is_im_enabled(home)
    aux_mode = _aux_mode(home)
    _write_rules_for_peer(home, "peerA", im_enabled=im_enabled, aux_mode=aux_mode)
    _write_rules_for_peer(home, "peerB", im_enabled=im_enabled, aux_mode=aux_mode)
    _write_rules_for_aux(home, aux_mode=aux_mode)
    _write_rules_for_foreman(home)
    if aux_mode == "on":
        try:
            ensure_aux_section(home)
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
        "- You act as the user's proxy. Speak in the user's voice.",
        "- Each run is non‑interactive and time‑boxed. Do one useful thing or write one short directive.",
        "",
        "Timer & Non‑overlap",
        "- The orchestrator runs you on a fixed interval and never overlaps runs.",
        "- Keep long work in files; keep messages short.",
        "",
        "Write‑to Path (single hand‑off)",
        "- Write exactly one message per run to: `.cccc/mailbox/foreman/to_peer.md`.",
        "- Put one routing header at the top:",
        "  To: Both|PeerA|PeerB  (default Both)",
        "- Wrap the body with `<TO_PEER> ... </TO_PEER>`.",
        "",
        "Anchors to read (skim then decide)",
        "- Project brief: PROJECT.md",
        "- Portfolio board: docs/por/POR.md (Now/Next/Risks)",
        "- Active tasks: docs/por/T*/SUBPOR.md (Owner/Next/Acceptance)",
        "- Peer rules: .cccc/rules/PEERA.md, .cccc/rules/PEERB.md",
        "- Evidence/work roots: docs/evidence/**, .cccc/work/**",
        "",
        "Routing defaults & backlog",
        "- Route architecture/alignment/risks to PeerA; implementation/experiments to PeerB.",
        "- If many pending inbox items exist, remind to process oldest‑first, then propose one smallest next step aligned to POR/SUBPOR.",
        "",
        "Boundaries",
        "- Do not paste long logs in messages; reference repo paths only.",
        "- Do not modify orchestrator code/policies; do not declare 'done'.",
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
            return txt
    except Exception:
        pass
    # Fallback: construct a minimal banner if rules are not yet available
    por_file = por_path(home)
    rules_path = (home/"rules"/("PEERA.md" if (peer.lower()=="peera" or peer=="peerA") else "PEERB.md")).as_posix()
    other = "peerB" if (peer.lower()=="peera" or peer=="peerA") else "peerA"
    lines = [
        "CCCC Runtime SYSTEM (full)",
        f"* You are {peer}. Collaborate as equals with {other}.",
        f"* POR: {por_file.as_posix()} (single source; update when direction changes).",
        f"* Rules: {rules_path} - follow this document; keep <TO_USER>/<TO_PEER> wrappers; end with exactly one fenced insight block.",
        "",
    ]
    try:
        lines.append(_runtime_bindings_one_liner(home))
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

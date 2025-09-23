# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

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
        "settings/governance.yaml",
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

def _aux_mode(home: Path) -> str:
    conf_path = home/"settings"/"cli_profiles.yaml"
    conf = _read_yaml_or_json(conf_path) if conf_path.exists() else {}
    aux_section = conf.get("aux") if isinstance(conf.get("aux"), dict) else {}
    mode_val = aux_section.get("mode")
    if isinstance(mode_val, bool):
        mode_raw = "on" if mode_val else "off"
    else:
        mode_raw = str(mode_val or "off").lower().strip()
    if mode_raw in ("on", "auto", "key_nodes", "keynodes", "manual", "true"):
        return "on"
    return "off"

def _conversation_reset(home: Path) -> Tuple[str, Optional[int]]:
    gv = home/"settings"/"governance.yaml"
    if not gv.exists():
        return "compact", None
    d = _read_yaml_or_json(gv)
    c = d.get("conversation") if isinstance(d.get("conversation"), dict) else {}
    r = c.get("reset") if isinstance(c.get("reset"), dict) else {}
    policy = str(r.get("policy") or "compact").lower().strip()
    if policy not in ("compact", "clear"):
        policy = "compact"
    try:
        interval = int(r.get("interval_handoffs") or 0)
    except Exception:
        interval = 0
    return policy, (interval if interval > 0 else None)

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
        "  - Global view first: goal -> constraints -> options -> cheapest decisive probe.",
        "  - Evidence-first; chat never changes state.",
        "  - Taste and clarity: simple, tight, clean.",
        "  - Anti-laziness: refuse low-signal output; prefer decisive micro-moves.",
        "- Lean collaboration creed (applies everywhere)",
        "  - Align before you act; one decidable next step per message (<=30 minutes).",
        "  - Done = has verifiable evidence (commit/test/log). Silence beats a vacuous ACK.",
        "  - Write one line of the strongest opposite view for every claim; do not rubber-stamp.",
        "  - If foundations are crooked or the artifact is low quality, refuse review and propose the smallest re-do-from-scratch probe instead of patching a mess.",
    ]
    if aux_enabled:
        ch1 += [
            "- On-demand helper: Aux (PeerC) - purpose & direction",
            "  - Use Aux when a decoupled subtask or high-level sanity sweep is cheaper offloaded than done inline. You integrate the outcome.",
            "  - Mode: on - orchestrator may issue FROM_SYSTEM reminders around key decisions; respond promptly and summarize outcomes for your peer.",
        ]
    else:
        ch1 += [
            "- Aux availability",
            "  - Aux is disabled for this run. You and your peer handle strategy checks and heavy lifting directly until you enable Aux.",
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
        "  - 0 Read POR (goal/guardrails/bets/roadmap).",
        "  - 1 Pick exactly one smallest decisional probe.",
        "  - 2 Build; keep changes small and reversible.",
        "  - 3 Validate (command + 1-3 stable lines; cite exact paths/line ranges).",
        "  - 4 Write the message using the skeleton in Chapter 4.",
        "  - 5 Add one insight (WHY + Next + refs); do not repeat the body.",
        "  - 6 If direction changed, update POR and the relevant SUBPOR.",
        "- Evidence and change budget",
        "  - Only tests/logs/commits count as evidence. Avoid speculative big refactors; always show the smallest reproducible check.",
        "- Pivot and refusal (signals and judgment; not quotas)",
        "  - Pivot when two or more hold: negative evidence piles up; a simpler alternative is clearly smaller or safer; infra cost exceeds benefit; guardrails are repeatedly hit; roadmap Now/Next has shifted.",
        "  - Refuse and rebuild: when foundations are bad or artifact quality is low, refuse review and propose the smallest from-scratch probe instead of patching a mess.",
        "- NUDGE behavior (one-liner)",
        "  - On [NUDGE]: read oldest inbox item -> act -> move to processed/ -> next; reply only when blocked.",
    ]
    if aux_enabled:
        ch3 += [
            "- Aux (PeerC) - Default Delegation {#aux}",
            "  - Default: delegate execution of any decoupled sub-task to Aux; you manage review/revise and integration, and you own the final evidence.",
            "  - If you choose not to use Aux, add one line in your insight - no-aux: <brief reason>. This is a soft nudge, not a gate.",
            '  - One-liner command: gemini -p "<detailed goal + instruction + context>@<paths>" --yolo',
        ]
    else:
        ch3 += [
            "- Aux {#aux}",
            "  - Aux is disabled. Collaborate directly or escalate to the user when you need a second opinion.",
        ]

    ascii_rule = "  - Temporary constraint (PeerA only): content in to_user.md and to_peer.md must be ASCII-only (7-bit). Use plain ASCII punctuation." if is_peera else None
    update_targets = [to_peer]
    if is_peera:
        update_targets.insert(0, to_user)
    target_list = ", ".join(update_targets)

    ch4 = [
        "",
        "4) Communicate (message skeleton and file I/O)",
        "- Writing rules (strict)",
        f"  - Update-only: always overwrite {target_list}; do NOT append or create new variants.",
        "  - Encoding: UTF-8 (no BOM).",
        "  - Do not claim done unless acceptance is checked in SUBPOR and you include minimal verifiable evidence (tests/stable logs/commit refs).",
    ]
    if ascii_rule:
        ch4.append(ascii_rule)
    ch4 += [
        "  - Keep <TO_USER>/<TO_PEER> wrappers; end with exactly one fenced `insight` block.",
        "  - Do not modify orchestrator code/config/policies.",
        "- Message skeleton (ready to copy) {#message-skeleton}",
        "  <TO_PEER>",
        "  Outcome: <one-line conclusion> ; Why: <one-line reason> ; Opposite: <one-line strongest opposite>",
        "  Evidence: <<=3 lines stable output or commit refs>",
        "  Next: <single, decidable, <=30 minutes>",
        "  </TO_PEER>",
        "  ```insight",
        "  to: peerA|peerB",
        "  kind: ask|counter|evidence|revise|risk",
        "  task_id: T000123",
        "  refs: [\"commit:abc123\", \"cmd:pytest -q::OK\", \"log:.cccc/work/...#L20-32\"]",
        "  next: <one next step>",
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
        "  - User-facing (when used): <=6 lines; conclusion first, then evidence paths; questions must be decidable.",
    ]
    if im_enabled:
        route_prefix = "a" if is_peera else "b"
        ch4 += [
            "- IM routing & passthrough (active) {#im}",
            "  - Chat routing: `a:`, `b:`, `both:` or `/a`, `/b`, `/both` from IM land in your mailbox; process them like any other inbox item.",
            f"  - Direct CLI passthrough: `{route_prefix}! <command>` runs inside your CLI pane; capture outputs in .cccc/work/** when they matter.",
            "  - System commands such as /focus, /reset, /aux, /review from IM arrive as <FROM_SYSTEM> notes; act and report in your next turn.",
        ]

    text = "\n".join([f"# {role_name} Rules (Generated)", "", *ch1, *ch2, *ch3, *ch4, ""])
    target = _rules_dir(home)/rules_filename
    target.write_text(text, encoding="utf-8")
    return target


def _write_rules_for_aux(home: Path, *, aux_mode: str) -> Path:
    por_rel = por_path(home).as_posix()
    session_root = (home/"work"/"aux_sessions").as_posix()

    ch1 = [
        "1) Role - Activation - Expectations",
        "- You are Aux (PeerC), the on-demand third peer. PeerA/PeerB summon you for strategic corrections and heavy execution that stay reversible.",
        "- Activation: orchestrator drops a bundle under .cccc/work/aux_sessions/<session-id>/ containing POR.md, notes.txt, peer_message.txt, and any extra context.",
        "- Rhythm: operate with the same evidence-first standards as the primary peers - small, testable moves and explicit next checks.",
    ]
    if aux_mode == "on":
        ch1.append("- Mode: on - orchestrator may summon you automatically around contract/sign-off moments. Treat those requests as high priority.")
    else:
        ch1.append("- Mode: off - you will only run when a peer explicitly invokes you. Stay ready for ad-hoc calls.")

    ch2 = [
        "",
        "2) Critical References & Inputs",
        f"- POR.md - single source of direction (path: {por_rel}). Always reconcile the bundle against the latest POR before proposing actions.",
        f"- Session bundle - {session_root}/<session-id>/",
        "  - Read notes.txt first: it captures the ask, expectations, and any suggested commands.",
        "  - peer_message.txt (when present) mirrors the triggering CLAIM/COUNTER/EVIDENCE; use it to align tone and scope.",
        "  - Additional artifacts (logs, datasets) live alongside; cite exact paths in your outputs.",
        "- This rules document - .cccc/rules/PEERC.md. Reference anchors from here in any summary you produce for the peers.",
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

    text = "\n".join(["# PEERC Rules (Generated)", "", *ch1, *ch2, *ch3, *ch4, ""])
    target = _rules_dir(home)/"PEERC.md"
    target.write_text(text, encoding="utf-8")
    return target

def ensure_rules_docs(home: Path):
    # Generate rules if missing or when config hash changed
    h = _calc_rules_hash(home)
    state = _state_dir(home)
    stamp = state/"rules_hash.json"
    old = {}
    try:
        old = json.loads(stamp.read_text(encoding="utf-8"))
    except Exception:
        old = {}
    if not (home/"rules"/"PEERA.md").exists() or not (home/"rules"/"PEERB.md").exists() or not (home/"rules"/"PEERC.md").exists() or old.get("hash") != h:
        ensure_por(home)  # make sure POR exists for path rendering
        im_enabled = _is_im_enabled(home)
        aux_mode = _aux_mode(home)
        _write_rules_for_peer(home, "peerA", im_enabled=im_enabled, aux_mode=aux_mode)
        _write_rules_for_peer(home, "peerB", im_enabled=im_enabled, aux_mode=aux_mode)
        _write_rules_for_aux(home, aux_mode=aux_mode)
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


def _ensure_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def weave_system_prompt(home: Path, peer: str, por: Optional[Dict[str, Any]] = None) -> str:
    """Minimal SYSTEM: role, POR, rules path - no duplication."""
    peer = (peer or "peerA").strip()
    try:
        ensure_rules_docs(home)
    except Exception:
        pass
    por_file = por_path(home)
    rules_path = (home/"rules"/("PEERA.md" if (peer.lower()=="peera" or peer=="peerA") else "PEERB.md")).as_posix()
    other = "peerB" if (peer.lower()=="peera" or peer=="peerA") else "peerA"
    return "\n".join([
        "CCCC Runtime SYSTEM (minimal)",
        f"* You are {peer}. Collaborate as equals with {other}.",
        f"* POR: {por_file.as_posix()} (single source; update when direction changes).",
        f"* Rules: {rules_path} - follow this document; keep <TO_USER>/<TO_PEER> wrappers; end with exactly one fenced insight block.",
        "",
    ])


def weave_preamble(home: Path, peer: str, por: Optional[Dict[str, Any]] = None) -> str:
    """
    Preamble text used for the first user message - identical source as SYSTEM
    to ensure single-source truth. By default returns weave_system_prompt.
    """
    return weave_system_prompt(home, peer, por)

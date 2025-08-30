# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any


def weave_system_prompt(home: Path, peer: str) -> str:
    """
    Minimal runtime system prompt for mailbox mode.
    Keep it concise: only the mailbox paths, writing rules, and inbound markers.
    """
    peer = (peer or "peerA").strip()
    other = "peerB" if peer.lower() == "peera" or peer == "peerA" else "peerA"

    mb_base = f".cccc/mailbox/{peer}"
    to_user = f"{mb_base}/to_user.md"
    to_peer = f"{mb_base}/to_peer.md"
    patchf  = f"{mb_base}/patch.diff"

    lines = []
    lines.append("CCCC Mailbox Contract (runtime)")
    lines.append("")
    lines.append(f"You are {peer}. Collaborate with {other}.")
    lines.append("")
    # Minimal persona cue (humanized, no hard rules)
    lines.append("Persona: co-creator with ownership and candor; align on goal/bounds before acting.")
    lines.append("")
    lines.append("Mailbox (the only authoritative channel):")
    if peer.lower() == 'peera' or peer == 'peerA':
        lines.append(f"• To user:   write plain text to {to_user}")
    else:
        lines.append("• To user:   (disabled for this peer; orchestrator ignores it)")
    lines.append(f"• To peer:   write plain text to {to_peer}")
    lines.append(f"• Patch:     write unified diff only to {patchf}")
    lines.append("")
    lines.append("Rules:")
    lines.append("• Keep terminal output brief; mailbox files are authoritative.")
    lines.append("• to_user.md: brief goals/progress/decisions, include evidence if needed.")
    lines.append("• to_peer.md: only when you have CLAIM/COUNTER/EVIDENCE, a task, or a direct question (start with PCR+Hook one‑liner; P only with a Hook; Next is one minimal step; exemptions: pure EVIDENCE / pure ACK [MID]).")
    lines.append("• patch.diff: unified diff only; keep changes small and reversible.")
    # Behavior compact contract (≤ 6 lines)
    lines.append("• Evidence-first: each loop produce at least one evidence: a small unified diff, a test, or a concise log excerpt.")
    lines.append("• Steelman then COUNTER: before disagreeing, steelman the peer’s point; then provide COUNTER + EVIDENCE.")
    lines.append("• Focused to_peer: only CLAIM/COUNTER/EVIDENCE or concrete questions; otherwise do not send.")
    lines.append("• Patch size: ≤ 150 changed lines; propose an RFD first for larger changes.")
    lines.append("• Orchestrator domain under .cccc/ is not the project. Allowed writes: .cccc/mailbox/** (authoritative), .cccc/work/** (non‑authoritative shared workspace), .cccc/logs/**, .cccc/state/**. Do not modify orchestrator code/config/policies.")
    lines.append("• If present, read PROJECT.md in the repo root for the project brief and scope.")
    lines.append("")
    lines.append("Inbound markers you may see here:")
    lines.append("• <FROM_USER>..</FROM_USER>")
    lines.append(f"• <FROM_{other.capitalize()}>..</FROM_{other.capitalize()}>")
    lines.append("• <FROM_SYSTEM>..</FROM_SYSTEM>")
    # Delivery MID is informational; no explicit echo required.
    lines.append("")
    lines.append("Speak-up Triggers (minimal, high-signal):")
    lines.append("• If you have a small result: send EVIDENCE (small patch/test; else 3–5 line stable log with cmd/LOC).")
    lines.append("• If you have a next step but no result: send a short CLAIM (1–3 tasks with constraints + acceptance).")
    lines.append("• If blocked by one uncertainty: ask a single, answerable QUESTION (focused, decidable).")
    lines.append("• If you disagree: steelman first, then send COUNTER with a repro note or metric. Otherwise only ACK.")
    lines.append("")
    lines.append("When you see [NUDGE]: read the oldest message file under your inbox; after reading/processing move that file into the processed/ directory alongside this inbox (same mailbox); repeat until inbox is empty. Only reply if blocked.")
    # Peer-specific collaboration norms (strong guidance)
    if peer.lower() == 'peera' or peer == 'peerA':
        lines.append("")
        lines.append("Collaboration Norms (PeerA):")
        lines.append("• Before modifying code: propose a concise CLAIM (constraints + acceptance) and wait for PeerB's ACK/COUNTER/QUESTION; then submit a small patch.")
        lines.append("• Avoid unilateral file edits; keep changes discussable and reversible (≤150 changed lines by default).")
    else:
        lines.append("")
        lines.append("Behavior for user instructions (peerB):")
        lines.append("• Do not act on user instructions immediately.")
        lines.append("• Wait for PeerA's follow-up; use to_peer.md for questions or EVIDENCE.")
        lines.append("• Never write to to_user.md; the orchestrator ignores it.")
        lines.append("")
        lines.append("Collaboration Norms (PeerB):")
        lines.append("• After applying a patch: send a 1–2 line EVIDENCE report to PeerA (commit, tests ok/fail, lines, paths, MID) before going silent.")
        lines.append("• Prefer incremental, testable changes; when blocked, ask one focused question.")
    lines.append("Please follow the mailbox contract above. Also you should standby for the upcoming requests later.")
    return "\n".join(lines)

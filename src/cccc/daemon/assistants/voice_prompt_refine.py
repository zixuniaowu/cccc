from __future__ import annotations

from typing import Any


def voice_composer_context_lines(value: Any) -> list[str]:
    context = value if isinstance(value, dict) else {}
    lines: list[str] = []
    recipients = context.get("recipients")
    if isinstance(recipients, list):
        clean_recipients = [str(item).strip() for item in recipients if str(item).strip()]
        if clean_recipients:
            lines.append(f"Recipients: {', '.join(clean_recipients[:12])}")
    else:
        recipient_text = str(context.get("recipients") or "").strip()
        if recipient_text:
            lines.append(f"Recipients: {recipient_text[:240]}")
    for label, key in (
        ("Message mode", "message_mode"),
        ("Priority", "priority"),
        ("Reply required", "reply_required"),
        ("Reply target", "reply_target"),
        ("Quoted reference", "quoted_reference"),
    ):
        raw = context.get(key)
        if raw in (None, "", []):
            continue
        lines.append(f"{label}: {str(raw)[:240]}")
    return lines


def build_voice_prompt_refine_input_text(
    *,
    composer_text: str,
    voice_transcript: str,
    operation: str,
    composer_context: Any,
) -> str:
    parts = [
        "Task: refine the user's chat composer prompt.",
        f"Operation: {operation or 'replace_with_refined_prompt'}",
        "",
        "Execution rules:",
        "- Output only the final polished prompt text that should be placed into the composer.",
        "- Do not describe what the user said, do not narrate your edits, and do not mention these instructions.",
        "- Do not write meta lead-ins such as 'Please help me', 'The user wants', '根据以下要求', or 'Here is the refined prompt'.",
        "- Preserve the user's intent, constraints, tone, and any explicit asks.",
        "- Improve clarity, structure, completeness, and actionability.",
        "- If the spoken input adds missing constraints or corrections, integrate them into the final prompt naturally.",
        "- If the composer already contains useful structure, keep and improve it instead of rewriting into a different task.",
    ]
    context_lines = voice_composer_context_lines(composer_context)
    if context_lines:
        parts.extend(["", "Composer context:", *context_lines])
    parts.extend(
        [
            "",
            "Current composer draft:",
            composer_text or "(empty)",
            "",
            "Spoken input:",
            voice_transcript,
            "",
            "Return only the ready-to-send refined prompt.",
        ]
    )
    return "\n".join(parts)

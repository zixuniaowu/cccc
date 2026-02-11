import requests, time, sys, os, subprocess, tempfile
from datetime import datetime, timezone

GROUP_ID = os.environ.get("CCCC_GROUP_ID", "g_878b8bbd4747")
ACTOR_ID = os.environ.get("CCCC_ACTOR_ID", "perA")
API = os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")

SYSTEM_PROMPT = (
    "You are perA, an AI assistant in the CCCC project (working dir: C:/Users/zixun/dev/cccc). "
    "You can read/write files, run commands, and help with coding tasks. "
    "Reply in the SAME language as the user (usually Chinese). "
    "IMPORTANT: Focus on the user's CURRENT message. History is just for context. "
    "When asked to create code or files, USE YOUR TOOLS to actually do it. "
    "After completing a task, give a brief spoken summary (2-4 sentences) of what you did. "
    "Do NOT repeat greetings if you already greeted in history. "
    "Do NOT talk about the CCCC project itself unless explicitly asked."
)

MAX_AGE_SEC = 60
HISTORY_LIMIT = 6  # fewer history lines to reduce noise
CLAUDE_TIMEOUT = 120  # longer timeout for coding tasks


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def fetch_inbox():
    try:
        r = requests.get(f"{API}/groups/{GROUP_ID}/inbox/{ACTOR_ID}", params={"limit": 20}, timeout=5)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("result", {}).get("messages", [])
    except Exception as e:
        print(f"[poller] inbox error: {e}", file=sys.stderr, flush=True)
        return []


def fetch_chat_history() -> str:
    """Fetch recent chat messages and format as conversation context."""
    try:
        r = requests.get(
            f"{API}/groups/{GROUP_ID}/ledger/search",
            params={"kind": "chat", "limit": HISTORY_LIMIT},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return ""
        events = data.get("result", {}).get("events", [])
    except Exception:
        return ""

    lines = []
    for evt in events:
        data = evt.get("data", {})
        by = data.get("by") or evt.get("by", "")
        text = (data.get("text") or "").strip()
        if not text:
            continue
        if by == "user":
            lines.append(f"User: {text}")
        elif by == ACTOR_ID:
            lines.append(f"You: {text}")
    return "\n".join(lines)


def send_reply(text, reply_to=None):
    try:
        payload = {
            "text": text,
            "by": ACTOR_ID,
            "to": ["user"],
            "priority": "normal",
            "src_event_id": reply_to or "",
        }
        r = requests.post(f"{API}/groups/{GROUP_ID}/send", json=payload, timeout=5)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[poller] send error: {e}", file=sys.stderr, flush=True)
        return False


def generate_reply(prompt: str) -> str:
    """Call Claude CLI with conversation history via PowerShell pipe."""
    prompt = prompt.strip()
    if not prompt:
        return ""

    history = fetch_chat_history()
    full_prompt = f"{SYSTEM_PROMPT}\n\n"
    if history:
        full_prompt += f"Recent conversation:\n{history}\n\n"
    full_prompt += f"--- CURRENT REQUEST ---\n{prompt}"

    env = dict(**os.environ)
    npm_bin = r"C:\\Users\\zixun\\AppData\\Roaming\\npm"
    env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"

    print(f"[poller] calling claude for: {prompt[:60]}", flush=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(full_prompt)
        tmp_path = f.name
    try:
        # Pass prompt as CLI arg (not pipe) so stdin stays free for tool use
        ps_cmd = [
            "powershell", "-NoLogo", "-NonInteractive", "-Command",
            f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; claude -p $p --no-session-persistence --dangerously-skip-permissions",
        ]
        res = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_TIMEOUT,
            env=env,
        )
        if res.returncode == 0 and res.stdout and res.stdout.strip():
            print(f"[poller] claude replied: {res.stdout.strip()[:80]}", flush=True)
            return res.stdout.strip()
        if res.stderr and res.stderr.strip():
            print(f"[poller] claude stderr: {res.stderr.strip()[:200]}", file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        print(f"[poller] claude timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[poller] claude error: {e}", file=sys.stderr, flush=True)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return ""


def simplify_reply(text: str, max_chars: int = 600) -> str:
    """Strip markdown styling and keep it short for TTS/UI."""
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("__", "").replace("*", "")
    cleaned = cleaned.replace("`", "").replace("```", "")
    for prefix in ("You:", "Assistant:", "助手:", "You：", "Assistant："):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars].rsplit("。", 1)[0] or cleaned[: max_chars]
        cleaned = cleaned.strip()
        if not cleaned.endswith("。"):
            cleaned += "。"
    return cleaned.strip()


def mark_read(event_id):
    try:
        payload = {"event_id": event_id, "by": ACTOR_ID}
        r = requests.post(f"{API}/groups/{GROUP_ID}/inbox/{ACTOR_ID}/read", json=payload, timeout=5)
        r.raise_for_status()
    except Exception as e:
        print(f"[poller] mark_read error: {e}", file=sys.stderr, flush=True)


def main():
    print(f"[poller] started for actor={ACTOR_ID} group={GROUP_ID}", flush=True)
    while True:
        msgs = fetch_inbox()
        actionable = []
        for m in msgs:
            if m.get("kind") != "chat.message":
                mark_read(m.get("id"))
                continue
            data = m.get("data") or {}
            if data.get("by") == ACTOR_ID:
                mark_read(m.get("id"))
                continue
            ts = m.get("ts")
            if ts:
                age = time.time() - _parse_ts(ts)
                if age > MAX_AGE_SEC:
                    mark_read(m.get("id"))
                    continue
            text = (data.get("text") or "").strip()
            if not text:
                mark_read(m.get("id"))
                continue
            actionable.append(m)

        if actionable:
            for old in actionable[:-1]:
                mark_read(old.get("id"))
            latest = actionable[-1]
            text = (latest.get("data") or {}).get("text", "").strip()
            reply = simplify_reply(generate_reply(text))
            if reply:
                send_reply(reply, reply_to=latest.get("id"))
            else:
                print(f"[poller] empty reply for: {text[:40]}", file=sys.stderr, flush=True)
            mark_read(latest.get("id"))

        time.sleep(1)


if __name__ == "__main__":
    main()

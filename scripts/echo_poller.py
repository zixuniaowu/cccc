import json, requests, time, sys, os, subprocess, tempfile, threading
from datetime import datetime, timezone

GROUP_ID = os.environ.get("CCCC_GROUP_ID", "g_878b8bbd4747")
ACTOR_ID = os.environ.get("CCCC_ACTOR_ID", "perA")
API = os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")
AGENT_RUNTIME = os.environ.get("CCCC_AGENT_RUNTIME", "gemini").strip().lower()

SYSTEM_PROMPT = (
    "You are perA, a friendly AI companion in the CCCC voice agent system "
    "(working dir: C:/Users/zixun/dev/cccc). "
    "You speak naturally and conversationally, like a helpful friend. "
    "Reply in the SAME language as the user (usually Chinese). "
    "Keep replies concise (2-4 sentences) for voice readback — no bullet points or headers. "
    "You can read/write files, run commands, and help with coding tasks. "
    "When completing a task, give a brief spoken summary of what you did. "
    "IMPORTANT: Focus on the user's CURRENT message. History is context only. "
    "Do NOT repeat greetings if already greeted in history. "
    "Do NOT talk about the CCCC project itself unless explicitly asked."
)

MAX_AGE_SEC = 60
HISTORY_LIMIT = 6
AGENT_TIMEOUT = int(os.environ.get("CCCC_AGENT_TIMEOUT", "300"))  # seconds
PROGRESS_INTERVAL = 30  # seconds before sending "still processing" update


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


def send_thinking_status(reply_to=None):
    """Send a 'thinking' indicator message that the frontend will detect and not TTS."""
    send_reply("正在思考...", reply_to=reply_to)


def send_progress_status(reply_to=None):
    """Send a 'still processing' progress message for long-running tasks."""
    send_reply("仍在处理，请稍候...", reply_to=reply_to)


def download_attachment(url: str, dest_path: str) -> bool:
    """Download an attachment file from the API."""
    try:
        full_url = url if url.startswith("http") else f"{API.rsplit('/api', 1)[0]}{url}"
        r = requests.get(full_url, timeout=30)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[poller] download attachment error: {e}", file=sys.stderr, flush=True)
        return False


def _parse_gemini_response(raw: str) -> str:
    """Extract response text from Gemini JSON output."""
    text = (raw or "").strip()
    if not text:
        return ""

    # Primary path: full JSON payload
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            resp = str(doc.get("response") or "").strip()
            if resp:
                return resp
    except Exception:
        pass

    # Fallback: try first JSON object in mixed output
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            doc = json.loads(text[start : end + 1])
            if isinstance(doc, dict):
                resp = str(doc.get("response") or "").strip()
                if resp:
                    return resp
        except Exception:
            pass

    # Last fallback: return raw
    return text


def generate_reply(prompt: str, reply_to: str | None = None, image_paths: list[str] | None = None) -> str:
    """Call configured agent CLI with conversation history via PowerShell pipe.
    Sends progress updates for long-running tasks."""
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

    print(f"[poller] calling runtime={AGENT_RUNTIME} for: {prompt[:60]}", flush=True)

    # Progress timer: send "still processing" if Claude takes > PROGRESS_INTERVAL seconds
    progress_sent = threading.Event()

    def send_progress():
        if not progress_sent.is_set():
            progress_sent.set()
            send_progress_status(reply_to=reply_to)

    progress_timer = threading.Timer(PROGRESS_INTERVAL, send_progress)
    progress_timer.daemon = True
    progress_timer.start()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(full_prompt)
        tmp_path = f.name
    try:
        # Build runtime-specific command (keep stdin free for tool use).
        # NOTE: Gemini image attachment CLI behavior may vary; currently we only pass text prompt.
        if AGENT_RUNTIME == "gemini":
            ps_cmd = [
                "powershell", "-NoLogo", "-NonInteractive", "-Command",
                f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; gemini -p $p --yolo --output-format json",
            ]
        else:
            image_args = ""
            if image_paths:
                for img_path in image_paths:
                    image_args += f" '{img_path}'"
            ps_cmd = [
                "powershell", "-NoLogo", "-NonInteractive", "-Command",
                f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; claude -p $p{image_args} --no-session-persistence --dangerously-skip-permissions",
            ]
        res = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGENT_TIMEOUT,
            env=env,
        )
        if res.returncode == 0 and res.stdout and res.stdout.strip():
            if AGENT_RUNTIME == "gemini":
                parsed = _parse_gemini_response(res.stdout)
                print(f"[poller] gemini replied: {parsed[:80]}", flush=True)
                return parsed
            print(f"[poller] claude replied: {res.stdout.strip()[:80]}", flush=True)
            return res.stdout.strip()
        if res.stderr and res.stderr.strip():
            print(f"[poller] {AGENT_RUNTIME} stderr: {res.stderr.strip()[:200]}", file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        print(f"[poller] {AGENT_RUNTIME} timeout ({AGENT_TIMEOUT}s)", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[poller] {AGENT_RUNTIME} error: {e}", file=sys.stderr, flush=True)
    finally:
        progress_timer.cancel()
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
    print(
        f"[poller] started for actor={ACTOR_ID} group={GROUP_ID} runtime={AGENT_RUNTIME}",
        flush=True,
    )
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
            latest_id = latest.get("id")
            text = (latest.get("data") or {}).get("text", "").strip()

            # Immediately send "thinking" indicator
            send_thinking_status(reply_to=latest_id)

            # Check for image attachments
            image_paths = []
            attachments = (latest.get("data") or {}).get("attachments", [])
            if not attachments:
                # Also check top-level files field
                attachments = latest.get("files", [])
            for att in attachments:
                att_url = att.get("url") or att.get("path") or ""
                if not att_url:
                    continue
                ext = att_url.rsplit(".", 1)[-1].lower() if "." in att_url else "jpg"
                if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
                    continue
                tmp_img = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
                tmp_img.close()
                if download_attachment(att_url, tmp_img.name):
                    image_paths.append(tmp_img.name)
                else:
                    try:
                        os.unlink(tmp_img.name)
                    except Exception:
                        pass

            reply = simplify_reply(generate_reply(text, reply_to=latest_id, image_paths=image_paths or None))

            # Clean up temp image files
            for img_path in image_paths:
                try:
                    os.unlink(img_path)
                except Exception:
                    pass

            # Filter out "nothing interesting" screen capture replies
            if reply and "无特别发现" not in reply:
                send_reply(reply, reply_to=latest_id)
            elif not reply:
                print(f"[poller] empty reply for: {text[:40]}", file=sys.stderr, flush=True)
            mark_read(latest_id)

        time.sleep(1)


if __name__ == "__main__":
    main()

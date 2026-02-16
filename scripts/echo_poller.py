import json, requests, time, sys, os, subprocess, tempfile, threading, shutil
from datetime import datetime, timezone

GROUP_ID = os.environ.get("CCCC_GROUP_ID", "").strip()
ACTOR_ID = os.environ.get("CCCC_ACTOR_ID", "perA")
API = os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")
AGENT_RUNTIME = os.environ.get("CCCC_AGENT_RUNTIME", "gemini").strip().lower()
GEMINI_MODEL = os.environ.get("CCCC_GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

SYSTEM_PROMPT = (
    "You are perA, a real-time voice assistant. "
    "Reply in the SAME language as the user (usually Chinese). "
    "Keep replies concise and directly answer the current user message first. "
    "Do not output setup boilerplate like '准备好了/随时待命' unless user explicitly asks for status. "
    "If the user utterance is incomplete or ambiguous, ask one short clarification question. "
    "History may contain ASR noise; only use history when clearly relevant."
)

MAX_AGE_SEC = 60
HISTORY_LIMIT = 6
AGENT_TIMEOUT = int(os.environ.get("CCCC_AGENT_TIMEOUT", "300"))  # seconds
PROGRESS_INTERVAL = int(os.environ.get("CCCC_PROGRESS_INTERVAL", "30"))  # seconds before progress update


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# UX default: avoid chatty placeholder messages for voice Q&A.
# If needed, enable via env:
# - CCCC_SEND_THINKING_STATUS=1
# - CCCC_SEND_PROGRESS_STATUS=1
SEND_THINKING_STATUS = _env_bool("CCCC_SEND_THINKING_STATUS", default=False)
SEND_PROGRESS_STATUS = _env_bool("CCCC_SEND_PROGRESS_STATUS", default=False)
# Voice QA quality is better without noisy ASR history by default.
USE_CHAT_HISTORY = _env_bool("CCCC_USE_HISTORY", default=False)


def _resolve_cli_command(name: str) -> str:
    """Resolve CLI command robustly on Windows (prefer npm .cmd shims)."""
    appdata = os.environ.get("APPDATA", "").strip()
    candidates = []
    if appdata:
        npm_bin = os.path.join(appdata, "npm")
        candidates.extend(
            [
                os.path.join(npm_bin, f"{name}.cmd"),
                os.path.join(npm_bin, f"{name}.exe"),
                os.path.join(npm_bin, name),
            ]
        )
    for p in candidates:
        if os.path.isfile(p):
            return p
    return name


def _discover_group_id() -> str:
    """Best-effort group discovery when CCCC_GROUP_ID is not provided."""
    try:
        r = requests.get(f"{API}/groups", timeout=5)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return ""
        groups = data.get("result", {}).get("groups", []) or []
        if not groups:
            return ""
        # Prefer running group first.
        for g in groups:
            if bool(g.get("running")) and str(g.get("group_id") or "").strip():
                return str(g.get("group_id")).strip()
        first = groups[0]
        return str(first.get("group_id") or "").strip()
    except Exception:
        return ""


if not GROUP_ID:
    GROUP_ID = _discover_group_id()


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
    if not USE_CHAT_HISTORY:
        return ""
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

    def _is_noisy_history_text(text: str) -> bool:
        compact = "".join(str(text or "").split())
        if not compact:
            return True
        if len(compact) <= 2:
            return True
        if compact.lower() in {"you", "we", "ok", "hello", "test"}:
            return True
        if (
            "连通性测试" in compact
            or "PING_" in compact
            or "VOICECHK" in compact
            or "自动回复连通性测试" in compact
        ):
            return True
        lower = compact.lower()
        if (
            "firstcommand" in lower
            or "readywhenyouare" in lower
            or "setupcomplete" in lower
            or "readyforyourfirstcommand" in lower
        ):
            return True
        if (
            "准备好" in compact
            or "第一个指令" in compact
            or "随时待命" in compact
        ):
            return True
        if len(compact) <= 4:
            return True
        return False

    lines = []
    for evt in events:
        data = evt.get("data", {})
        by = data.get("by") or evt.get("by", "")
        text = (data.get("text") or "").strip()
        if not text:
            continue
        if _is_noisy_history_text(text):
            continue
        if by == "user":
            lines.append(f"User: {text}")
        elif by == ACTOR_ID:
            lines.append(f"You: {text}")
    if len(lines) > 4:
        lines = lines[-4:]
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
    if SEND_THINKING_STATUS:
        send_reply("正在思考...", reply_to=reply_to)


def send_progress_status(reply_to=None):
    """Send a 'still processing' progress message for long-running tasks."""
    if SEND_PROGRESS_STATUS:
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


def _looks_bad_reply(reply: str) -> bool:
    text = str(reply or "").strip()
    if not text:
        return True
    compact = "".join(text.split())
    lower = compact.lower()
    lower_plain = lower.strip(" .!。！？")
    bad_markers = (
        "firstcommand",
        "readywhenyouare",
        "setupcomplete",
        "第一个指令",
        "准备好接收",
        "请下指令",
        "cli_helptool",
        "toolisnotavailable",
        "iamunabletoansweryourquestion",
        "question\"willistillkeepsending",
        "iamsorry,icannotprovideweatherinformation",
        "iamsorry,icannotprovideweather",
    )
    if any(m in lower for m in bad_markers):
        return True
    if lower_plain in ("understood", "acknowledged", "ok", "okay"):
        return True
    if lower in ("understood.", "acknowledged.", "okay.", "好的，请讲。", "好的请讲"):
        return True
    return False


def _fallback_reply_from_prompt(prompt: str) -> str:
    p = str(prompt or "").strip()
    compact = "".join(p.split())
    if not compact:
        return "我没听清，你可以再说一遍吗？"
    if any(k in compact for k in ("天气", "东京天气", "气温")):
        return "我这边现在不能直接查实时天气。你要的话我可以帮你接入天气接口后再播报。"
    if any(k in compact for k in ("自动聆听", "关闭自动", "会不会继续发送")):
        return "按现在逻辑，关闭自动聆听后会立刻停识别并清空残留，不应该再自动发送。"
    if len(compact) <= 3:
        return "这句有点短，我没完全听清，你可以再说完整一点吗？"
    return "我收到了，你再具体一点，我给你更准确的回答。"


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _is_english_heavy(text: str) -> bool:
    s = str(text or "")
    if not s:
        return False
    letters = sum(1 for ch in s if ("a" <= ch.lower() <= "z"))
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return letters >= 12 and letters > cjk * 2


def _run_model_command(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    run_cwd = tempfile.mkdtemp(prefix="cccc_voice_")
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGENT_TIMEOUT,
            env=env,
            cwd=run_cwd,
        )
    finally:
        shutil.rmtree(run_cwd, ignore_errors=True)


def generate_reply(prompt: str, reply_to: str | None = None, image_paths: list[str] | None = None) -> str:
    """Call configured agent CLI with conversation history.
    Sends progress updates for long-running tasks."""
    prompt = prompt.strip()
    if not prompt:
        return ""

    history = fetch_chat_history()
    if AGENT_RUNTIME == "gemini":
        # Keep Gemini prompt as plain QA format to avoid "setup complete" style drift.
        full_prompt = f"问题：{prompt}\n答案："
    else:
        full_prompt = f"{SYSTEM_PROMPT}\n\n"
        if history:
            full_prompt += f"Recent conversation:\n{history}\n\n"
        full_prompt += f"--- CURRENT REQUEST ---\n{prompt}"

    env = dict(**os.environ)
    appdata = env.get("APPDATA", "").strip()
    npm_bin = os.path.join(appdata, "npm") if appdata else r"C:\\Users\\zixun\\AppData\\Roaming\\npm"
    env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"

    print(f"[poller] calling runtime={AGENT_RUNTIME} for: {prompt[:60]}", flush=True)

    progress_timer = None
    if SEND_PROGRESS_STATUS and PROGRESS_INTERVAL > 0:
        # Progress timer: send a status update for long-running requests.
        progress_sent = threading.Event()

        def send_progress():
            if not progress_sent.is_set():
                progress_sent.set()
                send_progress_status(reply_to=reply_to)

        progress_timer = threading.Timer(PROGRESS_INTERVAL, send_progress)
        progress_timer.daemon = True
        progress_timer.start()

    try:
        # Build runtime-specific command without shell string interpolation.
        # This avoids prompt parsing issues with long/multiline text.
        if AGENT_RUNTIME == "gemini":
            cmd = [_resolve_cli_command("gemini")]
            if GEMINI_MODEL:
                cmd.extend(["-m", GEMINI_MODEL])
            cmd.extend(["-p", full_prompt, "--yolo", "--output-format", "json"])
        else:
            cmd = [_resolve_cli_command("claude"), "-p", full_prompt]
            if image_paths:
                for img_path in image_paths:
                    cmd.append(img_path)
            cmd.extend(["--no-session-persistence", "--dangerously-skip-permissions"])
        res = _run_model_command(cmd, env)
        if res.returncode == 0 and res.stdout and res.stdout.strip():
            if AGENT_RUNTIME == "gemini":
                parsed = _parse_gemini_response(res.stdout)
                if _looks_bad_reply(parsed):
                    retry_prompt = f"只用中文直接回答，不要提设置或工具。问题：{prompt}\n回答："
                    retry_cmd = [_resolve_cli_command("gemini")]
                    if GEMINI_MODEL:
                        retry_cmd.extend(["-m", GEMINI_MODEL])
                    retry_cmd.extend(["-p", retry_prompt, "--yolo", "--output-format", "json"])
                    retry_res = _run_model_command(retry_cmd, env)
                    if retry_res.returncode == 0 and retry_res.stdout and retry_res.stdout.strip():
                        parsed_retry = _parse_gemini_response(retry_res.stdout)
                        if not _looks_bad_reply(parsed_retry):
                            parsed = parsed_retry
                if _contains_cjk(prompt) and _is_english_heavy(parsed):
                    parsed = _fallback_reply_from_prompt(prompt)
                compact_prompt = "".join(str(prompt or "").split())
                if any(k in compact_prompt for k in ("自动聆听", "关闭自动", "继续发送")) and any(
                    k in str(parsed) for k in ("无法回答", "不能回答", "不确定")
                ):
                    parsed = _fallback_reply_from_prompt(prompt)
                if any(k in compact_prompt for k in ("天气", "气温")) and any(
                    k in str(parsed).lower() for k in ("cannot", "unable", "sorry", "无法")
                ):
                    parsed = _fallback_reply_from_prompt(prompt)
                if _looks_bad_reply(parsed):
                    parsed = _fallback_reply_from_prompt(prompt)
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
        if progress_timer is not None:
            progress_timer.cancel()
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
        f"[poller] started for actor={ACTOR_ID} group={GROUP_ID} runtime={AGENT_RUNTIME} "
        f"thinking_status={int(SEND_THINKING_STATUS)} progress_status={int(SEND_PROGRESS_STATUS)}",
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

            # Optional thinking indicator (disabled by default)
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

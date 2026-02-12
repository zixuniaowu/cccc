"""
News broadcast agent — fetches news via Claude CLI + web search,
then sends to a CCCC group for the voice agent to read aloud.

Designed to be managed as a daemon subprocess (similar to IM bridge),
or run standalone.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

CLAUDE_TIMEOUT = 120
NEWS_PROMPT_TEMPLATE = (
    "You are a news briefing assistant. Search the web for the latest news about: {interests}. "
    "Provide a concise Chinese news briefing with 2-3 items. "
    "Format: Start with {prefix} prefix, then list each item as a short paragraph (1-2 sentences each). "
    "Total length must be under 300 characters. Be concise and informative. "
    "Focus on the most interesting and relevant items from the past few hours."
)


def _api_base() -> str:
    return os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")


def _actor_id() -> str:
    return os.environ.get("CCCC_ACTOR_ID", "perA")


def send_message(group_id: str, text: str) -> bool:
    """Send a message to the group."""
    try:
        payload = {
            "text": text,
            "by": _actor_id(),
            "to": ["user"],
            "priority": "normal",
        }
        r = requests.post(
            f"{_api_base()}/groups/{group_id}/send",
            json=payload,
            timeout=5,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[news] send error: {e}", file=sys.stderr, flush=True)
        return False


def fetch_news(interests: str, is_morning: bool = False) -> str:
    """Call Claude CLI with web search to get news summary."""
    prefix = "[早间简报]" if is_morning else "[新闻简报]"
    prompt = NEWS_PROMPT_TEMPLATE.format(interests=interests, prefix=prefix)

    env = os.environ.copy()
    # Ensure claude CLI is available
    npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
    if os.path.isdir(npm_bin):
        env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(prompt)
        tmp_path = f.name

    try:
        # Use PowerShell on Windows, sh on Unix
        if sys.platform == "win32":
            cmd = [
                "powershell", "-NoLogo", "-NonInteractive", "-Command",
                f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; "
                f"claude -p $p --no-session-persistence --dangerously-skip-permissions",
            ]
        else:
            cmd = [
                "sh", "-c",
                f'p=$(cat "{tmp_path}"); claude -p "$p" --no-session-persistence --dangerously-skip-permissions',
            ]

        print(f"[news] fetching news for: {interests}", flush=True)
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_TIMEOUT,
            env=env,
        )
        if res.returncode == 0 and res.stdout and res.stdout.strip():
            result = res.stdout.strip()
            print(f"[news] got briefing: {result[:80]}", flush=True)
            return result
        if res.stderr and res.stderr.strip():
            print(
                f"[news] claude stderr: {res.stderr.strip()[:200]}",
                file=sys.stderr,
                flush=True,
            )
    except subprocess.TimeoutExpired:
        print(
            f"[news] claude timeout ({CLAUDE_TIMEOUT}s)",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:
        print(f"[news] claude error: {e}", file=sys.stderr, flush=True)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return ""


def do_briefing(group_id: str, interests: str) -> None:
    """Perform a single news briefing cycle."""
    now = datetime.now()
    is_morning = now.hour < 10
    news = fetch_news(interests, is_morning=is_morning)
    if not news:
        print("[news] no news fetched, skipping", flush=True)
        return

    # Ensure it has the right prefix
    if not news.startswith("[") or (
        "新闻简报" not in news[:10] and "早间简报" not in news[:10]
    ):
        prefix = "[早间简报]" if is_morning else "[新闻简报]"
        news = f"{prefix} {news}"

    # Truncate if too long for voice
    if len(news) > 400:
        news = news[:400].rsplit("。", 1)[0]
        if not news.endswith("。"):
            news += "。"

    send_message(group_id, news)


def start_agent(
    group_id: str,
    interests: str = "AI,科技,编程",
    schedule: str = "8,11,14,17,20",
) -> None:
    """Main loop — runs until SIGTERM/SIGINT."""
    schedule_hours = [int(h.strip()) for h in schedule.split(",") if h.strip()]
    interests_list = interests.strip()

    print(f"[news] agent started — group={group_id}", flush=True)
    print(f"[news] interests: {interests_list}", flush=True)
    print(f"[news] schedule hours: {schedule_hours}", flush=True)

    # Graceful shutdown
    stop = False

    def handle_signal(signum: int, frame: object) -> None:
        nonlocal stop
        print(f"[news] received signal {signum}, stopping", flush=True)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Write PID file if running under daemon
    pid_path = os.environ.get("NEWS_AGENT_PID_PATH")
    if pid_path:
        try:
            Path(pid_path).write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    last_briefing_hour = -1

    while not stop:
        now = datetime.now()
        current_hour = now.hour

        if current_hour in schedule_hours and current_hour != last_briefing_hour:
            print(
                f"[news] triggering briefing at {now.strftime('%H:%M')}",
                flush=True,
            )
            last_briefing_hour = current_hour
            try:
                do_briefing(group_id, interests_list)
            except Exception as e:
                print(f"[news] briefing error: {e}", file=sys.stderr, flush=True)

        time.sleep(30)

    print("[news] agent stopped", flush=True)

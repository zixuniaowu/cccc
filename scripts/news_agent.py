"""
News broadcast agent for CCCC voice companion.

Periodically fetches news summaries via Claude CLI + web search,
then sends them to the CCCC group for the voice agent to read aloud.

Usage:
    PYTHONUTF8=1 python scripts/news_agent.py

Environment variables:
    CCCC_GROUP_ID   - Target group ID (default: g_878b8bbd4747)
    CCCC_ACTOR_ID   - Actor identity (default: perA)
    CCCC_API        - API base URL (default: http://127.0.0.1:8848/api/v1)
    NEWS_INTERESTS  - Comma-separated interest keywords (default: AI,科技,编程)
    NEWS_SCHEDULE   - Comma-separated hours for briefings in 24h format (default: 8,11,14,17,20)
"""

import requests, time, sys, os, subprocess, tempfile, sched
from datetime import datetime, timezone

GROUP_ID = os.environ.get("CCCC_GROUP_ID", "g_878b8bbd4747")
ACTOR_ID = os.environ.get("CCCC_ACTOR_ID", "perA")
API = os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")
INTERESTS = os.environ.get("NEWS_INTERESTS", "AI,科技,编程").split(",")
SCHEDULE_HOURS = [int(h) for h in os.environ.get("NEWS_SCHEDULE", "8,11,14,17,20").split(",")]

CLAUDE_TIMEOUT = 120  # news summaries shouldn't need as long
NEWS_PROMPT_TEMPLATE = (
    "You are a news briefing assistant. Search the web for the latest news about: {interests}. "
    "Provide a concise Chinese news briefing with 2-3 items. "
    "Format: Start with [新闻简报] prefix, then list each item as a short paragraph (1-2 sentences each). "
    "Total length must be under 300 characters. Be concise and informative. "
    "Focus on the most interesting and relevant items from the past few hours. "
    "If it's the first briefing of the day (morning), use [早间简报] prefix instead."
)


def send_message(text: str):
    """Send a message to the group."""
    try:
        payload = {
            "text": text,
            "by": ACTOR_ID,
            "to": ["user"],
            "priority": "normal",
        }
        r = requests.post(f"{API}/groups/{GROUP_ID}/send", json=payload, timeout=5)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[news] send error: {e}", file=sys.stderr, flush=True)
        return False


def fetch_news(is_morning: bool = False) -> str:
    """Call Claude CLI with web search to get news summary."""
    interests_str = "、".join(INTERESTS)
    prompt = NEWS_PROMPT_TEMPLATE.format(interests=interests_str)
    if is_morning:
        prompt += "\nThis is the morning briefing — use [早间简报] prefix."

    env = dict(**os.environ)
    npm_bin = r"C:\Users\zixun\AppData\Roaming\npm"
    env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(prompt)
        tmp_path = f.name

    try:
        ps_cmd = [
            "powershell", "-NoLogo", "-NonInteractive", "-Command",
            f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; claude -p $p --no-session-persistence --dangerously-skip-permissions",
        ]
        print(f"[news] fetching news for: {interests_str}", flush=True)
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
            result = res.stdout.strip()
            print(f"[news] got briefing: {result[:80]}", flush=True)
            return result
        if res.stderr and res.stderr.strip():
            print(f"[news] claude stderr: {res.stderr.strip()[:200]}", file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        print(f"[news] claude timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[news] claude error: {e}", file=sys.stderr, flush=True)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return ""


def do_briefing():
    """Perform a single news briefing cycle."""
    now = datetime.now()
    is_morning = now.hour < 10
    news = fetch_news(is_morning=is_morning)
    if news:
        # Ensure it has the right prefix
        if not news.startswith("[") or ("新闻简报" not in news[:10] and "早间简报" not in news[:10]):
            prefix = "[早间简报]" if is_morning else "[新闻简报]"
            news = f"{prefix} {news}"
        # Truncate if too long for voice
        if len(news) > 400:
            news = news[:400].rsplit("。", 1)[0]
            if not news.endswith("。"):
                news += "。"
        send_message(news)
    else:
        print("[news] no news fetched, skipping", flush=True)


def main():
    print(f"[news] agent started — interests: {','.join(INTERESTS)}", flush=True)
    print(f"[news] schedule hours: {SCHEDULE_HOURS}", flush=True)
    print(f"[news] group={GROUP_ID} actor={ACTOR_ID}", flush=True)

    last_briefing_hour = -1

    while True:
        now = datetime.now()
        current_hour = now.hour

        if current_hour in SCHEDULE_HOURS and current_hour != last_briefing_hour:
            print(f"[news] triggering briefing at {now.strftime('%H:%M')}", flush=True)
            last_briefing_hour = current_hour
            try:
                do_briefing()
            except Exception as e:
                print(f"[news] briefing error: {e}", file=sys.stderr, flush=True)

        # Sleep 60 seconds between checks
        time.sleep(60)


if __name__ == "__main__":
    # Allow manual trigger with --now flag
    if "--now" in sys.argv:
        print("[news] manual trigger", flush=True)
        do_briefing()
    else:
        main()

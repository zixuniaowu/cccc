"""
News broadcast agent — fetches news via Gemini CLI + web search,
then sends to a CCCC group for the voice agent to read aloud.

Streaming mode:
- Broadcasts one item at a time with natural pacing.
- Prefetches next items in parallel while the current one is being read.
- Keeps running until stopped from Web UI.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import requests

FETCH_TIMEOUT = 120
MIN_ITEM_DELAY = 9
MAX_ITEM_DELAY = 18
EMPTY_RETRY_DELAY = 12
PREFETCH_LOW_WATERMARK = 2
NEWS_PREFIX = "[新闻简报]"
MORNING_PREFIX = "[早间简报]"
MARKET_PREFIX = "[股市简报]"
AI_TECH_PREFIX = "[AI新技术说明]"

NEWS_PROMPT_TEMPLATE = (
    "请生成可直接语音播报的三栏目简报，结合兴趣关键词：{interests}。"
    "栏目包括："
    "1) 综合新闻(news) 3条；"
    "2) 股市简报(market) 2条，优先A股/港股/美股指数、行业或公司要点；"
    "3) AI新技术说明(ai_tech) 2条，解释新模型/新能力/新应用进展。"
    "每条一句话，≤80字，纯中文口语化，适合主播朗读。"
    "严禁包含任何网址URL、http、markdown、来源括号注释、英文来源标注。"
    "{exclude_clause}"
    "如某栏目暂无可靠新内容，该栏目返回空数组。"
    "只返回JSON对象，不要其他文字。"
    '格式: {"news":["..."],"market":["..."],"ai_tech":["..."]}'
)

BRIEF_CHANNELS: list[tuple[str, str, int]] = [
    ("news", NEWS_PREFIX, 3),
    ("market", MARKET_PREFIX, 2),
    ("ai_tech", AI_TECH_PREFIX, 2),
]

BRIEF_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "news": ("news", "headlines", "top_news", "新闻", "综合新闻"),
    "market": ("market", "stock", "stocks", "market_news", "股市", "股市简报"),
    "ai_tech": (
        "ai_tech",
        "ai",
        "aiTech",
        "ai_explain",
        "ai_summary",
        "新技术",
        "ai新技术",
        "AI新技术说明",
    ),
}

KNOWN_BRIEF_PREFIXES = (
    NEWS_PREFIX,
    MORNING_PREFIX,
    MARKET_PREFIX,
    AI_TECH_PREFIX,
)


def _api_base() -> str:
    return os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")


def _actor_id() -> str:
    return os.environ.get("CCCC_ACTOR_ID", "perA")


def _runtime() -> str:
    rt = str(os.environ.get("NEWS_AGENT_RUNTIME", "gemini")).strip().lower()
    return "claude" if rt == "claude" else "gemini"


def _build_runtime_command(tmp_path: str, runtime: str) -> list[str]:
    if runtime == "gemini":
        if sys.platform == "win32":
            return [
                "powershell",
                "-NoLogo",
                "-NonInteractive",
                "-Command",
                (
                    f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; "
                    "gemini -p $p --yolo --output-format json"
                ),
            ]
        return [
            "sh",
            "-c",
            f'p=$(cat "{tmp_path}"); gemini -p "$p" --yolo --output-format json',
        ]
    if sys.platform == "win32":
        return [
            "powershell",
            "-NoLogo",
            "-NonInteractive",
            "-Command",
            (
                f"$p = Get-Content -Raw -Encoding UTF8 '{tmp_path}'; "
                "claude -p $p --no-session-persistence --dangerously-skip-permissions"
            ),
        ]
    return [
        "sh",
        "-c",
        f'p=$(cat "{tmp_path}"); claude -p "$p" --no-session-persistence --dangerously-skip-permissions',
    ]


def _extract_gemini_response(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            resp = str(doc.get("response") or "").strip()
            if resp:
                return resp
    except Exception:
        pass

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

    return text


def _run_runtime_prompt(
    *,
    cmd: list[str],
    env: dict[str, str],
    runtime: str,
    stop_check: Optional[Callable[[], bool]] = None,
) -> str:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    start = time.time()
    out = ""
    err = ""
    while True:
        if stop_check and stop_check():
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            print("[news] fetch interrupted by stop request", flush=True)
            return ""
        try:
            out, err = proc.communicate(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            if time.time() - start > FETCH_TIMEOUT:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                print(
                    f"[news] {runtime} timeout ({FETCH_TIMEOUT}s)",
                    file=sys.stderr,
                    flush=True,
                )
                return ""

    if proc.returncode == 0 and out and out.strip():
        text = out.strip()
        if runtime == "gemini":
            return _extract_gemini_response(text)
        return text
    if err and err.strip():
        print(
            f"[news] {runtime} stderr: {err.strip()[:200]}",
            file=sys.stderr,
            flush=True,
        )
    return ""


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


def _strip_brief_prefix(text: str) -> str:
    s = str(text or "").strip()
    for prefix in KNOWN_BRIEF_PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix) :].strip()
    return s


def fetch_news_items(
    interests: str,
    reported: list[str],
    stop_check: Optional[Callable[[], bool]] = None,
) -> list[str]:
    """Call runtime CLI with web search, return prefixed multi-brief items."""
    exclude_clause = ""
    if reported:
        # Include up to 12 recent headlines to avoid repeats.
        recent = [_strip_brief_prefix(s) for s in reported[-12:]]
        recent = [s for s in recent if s]
        exclude_clause = "排除已报道内容：" + "；".join(recent) + "。"

    prompt = NEWS_PROMPT_TEMPLATE.format(
        interests=interests,
        exclude_clause=exclude_clause,
    )

    env = os.environ.copy()
    npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
    if os.path.isdir(npm_bin):
        env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CLAUDECODE", None)
    runtime = _runtime()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(prompt)
        tmp_path = f.name

    try:
        cmd = _build_runtime_command(tmp_path, runtime)
        print(f"[news] fetching news for: {interests} (runtime={runtime})", flush=True)
        raw = _run_runtime_prompt(
            cmd=cmd,
            env=env,
            runtime=runtime,
            stop_check=stop_check,
        )
        if raw:
            print(f"[news] raw response: {raw[:120]}", flush=True)
            return _parse_multi_brief_items(raw)
    except Exception as e:
        print(f"[news] {runtime} error: {e}", file=sys.stderr, flush=True)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return []


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [text](url) → keep text
_PAREN_URL_RE = re.compile(r"\（[^）]*https?://[^）]*\）")  # （来源: http...）
_BRACKET_RE = re.compile(r"\([^)]*https?://[^)]*\)")  # (source: http...)
_SOURCE_TAG_RE = re.compile(r"[（(](?:来源|source|via)[：:][^）)]*[）)]", re.IGNORECASE)


def _clean_item(s: str) -> str:
    """Strip URLs, markdown links, source tags from a news item."""
    s = _MD_LINK_RE.sub(r"\1", s)  # [text](url) → text
    s = _PAREN_URL_RE.sub("", s)
    s = _BRACKET_RE.sub("", s)
    s = _SOURCE_TAG_RE.sub("", s)
    s = _URL_RE.sub("", s)
    # Remove leftover markdown: **bold**, *italic*, `code`
    s = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Remove trailing/leading punctuation artifacts
    s = s.strip("，、；: ")
    return s


def _is_good_item(s: str) -> bool:
    """Check if item is clean enough to broadcast."""
    if len(s) < 4:
        return False
    # Reject if still contains URLs
    if _URL_RE.search(s):
        return False
    # Reject near-empty punctuation-only fragments
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s):
        return False
    # Prefer Chinese content, but allow mixed EN proper nouns (OpenAI, Apple, etc.)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    if cjk == 0 and len(s) < 12:
        return False
    return True


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """Deduplicate items while preserving their original order."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _finalize_items(items: list[str], *, limit: int) -> list[str]:
    cleaned = [_clean_item(s) for s in items]
    strict = _dedupe_keep_order([s for s in cleaned if _is_good_item(s)])
    if strict:
        return strict[:limit]
    relaxed = _dedupe_keep_order([s for s in cleaned if s and not _URL_RE.search(s)])
    return relaxed[:limit]


def _parse_news_items(raw: str) -> list[str]:
    """Parse runtime response into list of clean news strings."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    items: list[str] = []

    # Try JSON parse (array or {response: "..."} wrapper)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            items = [str(s).strip() for s in parsed if str(s).strip()]
        elif isinstance(parsed, dict):
            resp = str(parsed.get("response") or "").strip()
            if resp:
                text = resp
    except json.JSONDecodeError:
        pass

    if not items and text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                items = [str(s).strip() for s in parsed if str(s).strip()]
        except json.JSONDecodeError:
            pass

    # Fallback: extract JSON array from text
    if not items:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, list):
                    items = [str(s).strip() for s in parsed if str(s).strip()]
            except json.JSONDecodeError:
                pass

    # Last fallback: split by lines
    if not items:
        lines = [l.strip().lstrip("0123456789.-、) ") for l in text.split("\n")]
        items = [l for l in lines if len(l) > 5]

    return _finalize_items(items, limit=5)


def _normalize_brief_field(value: object, *, limit: int) -> list[str]:
    if isinstance(value, list):
        raw = [str(s).strip() for s in value if str(s).strip()]
        return _finalize_items(raw, limit=limit)
    if isinstance(value, str):
        nested = _parse_news_items(value)
        if nested:
            return nested[:limit]
        return _finalize_items([value], limit=limit)
    return []


def _extract_json_object(text: str) -> Optional[dict[str, object]]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _parse_multi_brief_items(raw: str) -> list[str]:
    """Parse multi-channel brief JSON into prefixed items."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    parsed_obj: Optional[dict[str, object]] = None
    try:
        direct = json.loads(text)
        if isinstance(direct, dict):
            parsed_obj = direct
    except Exception:
        parsed_obj = _extract_json_object(text)

    if parsed_obj:
        out: list[str] = []
        for channel, prefix, limit in BRIEF_CHANNELS:
            aliases = BRIEF_KEY_ALIASES.get(channel, (channel,))
            value: object = []
            for key in aliases:
                if key in parsed_obj:
                    value = parsed_obj.get(key)
                    break
            channel_items = _normalize_brief_field(value, limit=limit)
            for item in channel_items:
                out.append(f"{prefix} {item}")
        deduped = _dedupe_keep_order(out)
        if deduped:
            return deduped

    # Backward compatible fallback: old array format -> regular news prefix.
    legacy = _parse_news_items(text)
    return [f"{NEWS_PREFIX} {s}" for s in legacy]


def _estimate_item_delay(item: str) -> int:
    compact = re.sub(r"\s+", "", item)
    chars = len(compact)
    # Rough speech pacing for Chinese TTS: ~4 chars/s + short breathing gap.
    est = int(round(chars / 4.0 + 2.0))
    return max(MIN_ITEM_DELAY, min(MAX_ITEM_DELAY, est))


def _sleep_with_stop(seconds: int, stop_check: Callable[[], bool]) -> bool:
    for _ in range(max(1, int(seconds))):
        if stop_check():
            return True
        time.sleep(1)
    return stop_check()


def start_agent(
    group_id: str,
    interests: str = "AI,科技,编程",
    **_kwargs: object,
) -> None:
    """Main loop — streams news items with concurrent prefetch until stopped."""
    interests_list = interests.strip()
    runtime = _runtime()

    print(f"[news] agent started - group={group_id}", flush=True)
    print(f"[news] interests: {interests_list}", flush=True)
    print(f"[news] runtime: {runtime}", flush=True)
    print("[news] mode: streaming + concurrent prefetch", flush=True)

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

    # Track reported headlines to avoid repeats across rounds
    reported: list[str] = []
    pending: list[str] = []
    pending_keys: set[str] = set()

    def stop_check() -> bool:
        return stop

    next_fetch_at = 0.0
    fetch_future: Optional[Future[list[str]]] = None

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="news-fetch") as pool:
        while not stop:
            now = time.time()

            # Start background fetch when due.
            if fetch_future is None and now >= next_fetch_at:
                snapshot = reported[-20:].copy()
                print("[news] fetching next batch...", flush=True)
                fetch_future = pool.submit(fetch_news_items, interests_list, snapshot, stop_check)

            # Collect fetched items when ready.
            if fetch_future is not None and fetch_future.done():
                try:
                    items = fetch_future.result()
                except Exception as e:
                    print(f"[news] fetch error: {e}", file=sys.stderr, flush=True)
                    items = []
                fetch_future = None

                added = 0
                for item in items:
                    key = item.strip()
                    if not key:
                        continue
                    if key in pending_keys or key in reported:
                        continue
                    pending.append(key)
                    pending_keys.add(key)
                    added += 1

                if added == 0:
                    next_fetch_at = time.time() + EMPTY_RETRY_DELAY
                    print("[news] no new items fetched, retrying soon", flush=True)
                else:
                    next_fetch_at = time.time() + 1
                    print(f"[news] queued {added} items (pending={len(pending)})", flush=True)

            # Broadcast one item at a time.
            if pending:
                item = pending.pop(0)
                pending_keys.discard(item)
                text = item
                if not any(text.startswith(prefix) for prefix in KNOWN_BRIEF_PREFIXES):
                    text = f"{NEWS_PREFIX} {text}"
                send_message(group_id, text)
                reported.append(text)

                if len(reported) > 80:
                    reported = reported[-50:]

                # If queue is running low, prefetch in parallel while user is listening.
                if fetch_future is None and len(pending) <= PREFETCH_LOW_WATERMARK:
                    snapshot = reported[-20:].copy()
                    print("[news] prefetching next batch...", flush=True)
                    fetch_future = pool.submit(fetch_news_items, interests_list, snapshot, stop_check)
                    next_fetch_at = time.time() + 1

                delay = _estimate_item_delay(_strip_brief_prefix(text))
                print(f"[news] sent: {text[:60]} (next in ~{delay}s)", flush=True)
                if _sleep_with_stop(delay, stop_check):
                    break
                continue

            # No pending items yet: tick and wait for fetch completion.
            if _sleep_with_stop(1, stop_check):
                break

    print("[news] agent stopped", flush=True)

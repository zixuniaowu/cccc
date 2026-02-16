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
import shutil
import signal
import subprocess
import sys
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
AI_LONG_PREFIX = "[AI长文说明]"
HORROR_PREFIX = "[恐怖故事]"

FACTUAL_GUARDRAIL = (
    "事实约束：仅允许使用可核验的公开信息；"
    "不允许编造、夸张、脑补或把预测当事实。"
    "若真实性不足或来源冲突，宁可不写该条并返回空数组。"
    "避免使用“已经完全实现”“像真人一样”等绝对化措辞。"
)

ALL_BRIEF_PROMPT_TEMPLATE = (
    "请生成可直接语音播报的三栏目简报，结合兴趣关键词：{interests}。"
    "栏目包括："
    "1) 综合新闻(news) 3条；"
    "2) 股市简报(market) 2条，优先A股/港股/美股指数、行业或公司要点；"
    "3) AI长文说明(ai_long) 1条，280~420字，3~5句，解释最新模型/能力/应用及影响。"
    "news/market每条一句话，≤80字，纯中文口语化，适合主播朗读。"
    + FACTUAL_GUARDRAIL +
    "严禁包含任何网址URL、http、markdown、来源括号注释、英文来源标注。"
    "{exclude_clause}"
    "如某栏目暂无可靠新内容，该栏目返回空数组。"
    "只返回JSON对象，不要其他文字。"
    '格式: {"news":["..."],"market":["..."],"ai_long":["..."]}'
)

NEWS_ONLY_PROMPT_TEMPLATE = (
    "请生成综合新闻口播稿，结合兴趣关键词：{interests}。"
    "输出3条，每条一句话，≤80字，纯中文口语化。"
    + FACTUAL_GUARDRAIL +
    "严禁包含任何网址URL、http、markdown、来源括号注释。"
    "{exclude_clause}"
    "只返回JSON数组，不要其他文字。"
    '格式: ["...","...","..."]'
)

MARKET_ONLY_PROMPT_TEMPLATE = (
    "请生成股市简报口播稿，结合兴趣关键词：{interests}。"
    "输出2条，优先A股/港股/美股指数、板块轮动、公司财报或政策影响。"
    "每条1~2句，≤110字，纯中文口语化。"
    + FACTUAL_GUARDRAIL +
    "严禁包含任何网址URL、http、markdown、来源括号注释。"
    "{exclude_clause}"
    "只返回JSON数组，不要其他文字。"
    '格式: ["...","..."]'
)

AI_LONG_PROMPT_TEMPLATE = (
    "请生成AI最新技术长文说明口播稿，结合兴趣关键词：{interests}。"
    "输出1条，280~420字，3~5句，结构为：进展 -> 原理/能力 -> 应用影响 -> 风险或边界。"
    "语言要求纯中文口语化，适合连续播报。"
    "严禁包含任何网址URL、http、markdown、来源括号注释。"
    "{exclude_clause}"
    "只返回JSON数组，不要其他文字。"
    '格式: ["..."]'
)

HORROR_STORY_PROMPT_TEMPLATE = (
    "请继续生成一段可直接语音播报的中文悬疑恐怖故事，结合主题关键词：{interests}。"
    "输出1条，130~220字，3~6句，叙事完整并留悬念。"
    "句式要短，节奏要有停顿感，可适量使用“忽然、就在这时、你听见”等口语转折。"
    "语气要有画面感和压迫感，但避免血腥或过度惊吓。"
    "优先营造环境音与细节：脚步、门缝、呼吸、灯光、金属摩擦、空房回声等。"
    "必须承接前情推进剧情，避免重复描述。"
    "严禁包含任何网址URL、http、markdown、来源括号注释。"
    "{exclude_clause}"
    "只返回JSON数组，不要其他文字。"
    '格式: ["..."]'
)

AI_LONG_ONCE_PROMPT_TEMPLATE = (
    "请一次性准备一篇可直接语音播报的中文专题长稿。"
    "主题关键词：{interests}。"
    "要求："
    "1) 总长度约2200~3000字，适合约10分钟语音播报；"
    "2) 输出10~14段，每段120~260字；"
    "3) 语言口语化、连贯，不要编号，不要标题党；"
    "4) 可包含必要背景解释和边界提醒；"
    "5) 严禁URL、http、markdown、来源括号注释。"
    "只返回JSON对象，不要其他文字。"
    '格式: {"title":"...","sections":["..."]}'
)

PREPARED_LONGFORM_SCRIPTS: dict[str, dict[str, object]] = {
    "cccc_intro_v1": {
        "aliases": (
            "cccc",
            "框架",
            "framework",
            "多agent",
            "协作",
            "daemon",
            "ledger",
        ),
        "title": "CCCC框架全景介绍：从多Agent协作到语音播报",
        "sections": [
            "今天这篇专题我们讲 CCCC 框架，不走实时快讯，而是用一份提前准备好的长稿来稳定播报。你可以把它理解成一个协作操作系统：上层是 Web、CLI 和 IM 等入口，中层是守护进程做调度，底层是消息账本、权限边界和状态管理。",
            "CCCC 的核心定位不是单个聊天机器人，而是多 Agent 协同控制中心。一个工作组里可以有协调角色，也可以有多个并行执行角色。每个角色既能接收任务，也能回传结果，所有动作进入同一事件流，方便你复盘谁在什么时候做了什么决策。",
            "从架构层看，daemon 负责统一写入和分发，避免多个进程同时改状态导致冲突；kernel 负责 group、actor、ledger、inbox 等基础能力；ports 提供 Web、CLI、IM Bridge 和 MCP 接入。分层之后，入口可以更换，但底层协作规则保持稳定。",
            "在协作机制上，CCCC 采用消息驱动而不是硬编码耦合调用。消息先写入 ledger，再按 to 字段路由给指定 actor。actor 处理后用 reply 或新消息回写，形成闭环。这个模型的价值是可审计、可回放、可恢复，短暂掉线也不会吞掉关键上下文。",
            "和常见自动化脚本不同，CCCC 把“已读确认”做成一等能力。Agent 需要显式 mark_read，系统才能推进游标。这样你能区分“送达了”还是“真正处理了”，在多人并行任务里尤其关键。它减少了任务悬空和误判完成的情况。",
            "运行时层面，CCCC 支持不同形态的执行器，包括交互式终端和 headless 模式。你可以按角色分配能力：一个角色偏实现代码，一个角色偏审查测试，再由协调角色汇总结论。框架重点不是绑定某个模型，而是稳定的协作编排能力。",
            "你现在常用的 Eyes 页面，是 CCCC 在交互层的一个延伸。视觉状态、语音收发、SSE 推送、摄像头网格和动作识别，最终都接到同一条消息通道。也就是说，这个会说话的界面只是入口形态变了，底层协作语义没有变。",
            "新闻播报、股市播报和长文播报看起来都在“说话”，但节奏模型完全不同。新闻和股市是短平快、偏实时；长文是专题讲解、强调连续结构。把长文改成预备稿后，播报稳定性更高，不容易被实时抓取抖动打断。",
            "从可维护性看，CCCC 的优势是状态收敛。进程 PID、配置、日志、群组元数据都有固定位置，异常时可以快速定位是哪一层出了问题。你前面遇到的停播问题，本质就是进程和状态没有完全对齐，这次改造就在补齐这块控制面。",
            "在团队场景里，这个框架适合把“需求澄清、实现、测试、汇报”串成一条流水。用户下达任务后，协调角色拆解并分发给不同 actor，再统一回收结果。你不用在多个工具之间跳转，协作证据天然沉淀在 ledger，后续复盘更轻。",
            "当然，框架也有边界。多 Agent 不会自动等于高质量，前提仍然是清晰任务协议和角色边界；外部依赖波动时要有降级路径，比如从实时生成回退到预置稿；提示词和流程也需要持续调参，这些都属于工程治理而不是一次性配置。",
            "最后给一个上手建议：先用最小角色集跑通闭环，再逐步增加自动化和外部桥接。先确保系统“可控、可停、可追踪”，再追求更复杂的智能编排。只要这三件事做实，CCCC 就会从一个工具页升级成长期可用的协作底座。",
        ],
    }
}

BRIEF_CHANNELS: list[tuple[str, str, int]] = [
    ("news", NEWS_PREFIX, 3),
    ("market", MARKET_PREFIX, 2),
    ("ai_long", AI_LONG_PREFIX, 1),
    ("horror", HORROR_PREFIX, 1),
]

BRIEF_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "news": ("news", "headlines", "top_news", "新闻", "综合新闻"),
    "market": ("market", "stock", "stocks", "market_news", "股市", "股市简报"),
    "ai_long": (
        "ai_long",
        "ai_long_form",
        "ai_longform",
        "ai_tech",
        "ai_tech",
        "ai",
        "aiTech",
        "ai_explain",
        "ai_summary",
        "长文",
        "长文说明",
        "AI长文说明",
        "新技术",
        "ai新技术",
        "AI新技术说明",
    ),
    "horror": (
        "horror",
        "horror_story",
        "ghost_story",
        "恐怖",
        "恐怖故事",
        "悬疑",
        "惊悚",
    ),
}

KNOWN_BRIEF_PREFIXES = (
    NEWS_PREFIX,
    MORNING_PREFIX,
    MARKET_PREFIX,
    AI_LONG_PREFIX,
    AI_TECH_PREFIX,
    HORROR_PREFIX,
)


MODE_SETTINGS: dict[str, dict[str, object]] = {
    "news": {
        "prefix": NEWS_PREFIX,
        "limit": 3,
        # CPU-only GPT-SoVITS latency is high; send slower to avoid TTS queue timeout.
        "min_delay": 24,
        "max_delay": 55,
        "chars_per_sec": 2.7,
        "gap": 6.0,
        "prompt": NEWS_ONLY_PROMPT_TEMPLATE,
    },
    "market": {
        "prefix": MARKET_PREFIX,
        "limit": 2,
        "min_delay": 12,
        "max_delay": 24,
        "chars_per_sec": 3.8,
        "gap": 3.0,
        "prompt": MARKET_ONLY_PROMPT_TEMPLATE,
    },
    "ai_long": {
        "prefix": AI_LONG_PREFIX,
        "limit": 1,
        "min_delay": 14,
        "max_delay": 50,
        "chars_per_sec": 3.9,
        "gap": 2.2,
        "prompt": AI_LONG_PROMPT_TEMPLATE,
    },
    "horror": {
        "prefix": HORROR_PREFIX,
        "limit": 1,
        # GPT-SoVITS on CPU can be much slower; keep story pacing conservative
        # to avoid text generation outrunning voice playback.
        "min_delay": 28,
        "max_delay": 96,
        "chars_per_sec": 2.3,
        "gap": 8.0,
        "prompt": HORROR_STORY_PROMPT_TEMPLATE,
    },
}


def _api_base() -> str:
    return os.environ.get("CCCC_API", "http://127.0.0.1:8848/api/v1")


def _actor_id() -> str:
    return os.environ.get("CCCC_ACTOR_ID", "perA")


def _runtime() -> str:
    rt = str(os.environ.get("NEWS_AGENT_RUNTIME", "gemini")).strip().lower()
    return "claude" if rt == "claude" else "gemini"


def _gemini_model() -> str:
    specific = str(os.environ.get("NEWS_AGENT_GEMINI_MODEL", "")).strip()
    if specific:
        return specific
    return str(os.environ.get("CCCC_GEMINI_MODEL", "gemini-2.5-flash-lite")).strip()


def _normalize_agent_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in ("horror", "horror_story", "story", "ghost"):
        return "horror"
    if m in ("news", "market", "ai_long", "horror", "all"):
        return m
    return "news"


def _agent_mode() -> str:
    return _normalize_agent_mode(os.environ.get("NEWS_AGENT_MODE", "news"))


def _ensure_runtime_path(env: dict[str, str]) -> None:
    current = str(env.get("PATH") or "")
    candidates: list[str] = []

    appdata = str(env.get("APPDATA") or os.environ.get("APPDATA") or "").strip()
    if appdata:
        candidates.append(os.path.join(appdata, "npm"))

    home_npm = Path.home() / "AppData" / "Roaming" / "npm"
    candidates.append(str(home_npm))

    local_appdata = str(env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        candidates.append(os.path.join(local_appdata, "npm"))

    merged: list[str] = []
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        if d not in merged:
            merged.append(d)
    if current:
        merged.append(current)
    if merged:
        env["PATH"] = os.pathsep.join(merged)


def _runtime_command_name(runtime: str) -> str:
    if os.name == "nt":
        return f"{runtime}.cmd"
    return runtime


def _runtime_available(runtime: str, env: dict[str, str]) -> bool:
    cmd = _runtime_command_name(runtime)
    path = str(env.get("PATH") or "")
    return bool(shutil.which(cmd, path=path) or shutil.which(runtime, path=path))


def _runtime_candidates(preferred: str, env: dict[str, str]) -> list[str]:
    order = [preferred, "gemini", "claude"]
    seen: set[str] = set()
    out: list[str] = []
    for rt in order:
        r = str(rt or "").strip().lower()
        if not r or r in seen:
            continue
        seen.add(r)
        if _runtime_available(r, env):
            out.append(r)
    return out


def _build_runtime_command(prompt: str, runtime: str, env: dict[str, str]) -> list[str]:
    exe = _runtime_command_name(runtime)
    path = str(env.get("PATH") or "")
    if not shutil.which(exe, path=path) and shutil.which(runtime, path=path):
        exe = runtime
    if runtime == "gemini":
        gemini_model = _gemini_model()
        model_args: list[str] = ["-m", gemini_model] if gemini_model else []
        return [
            exe,
            *model_args,
            "-p",
            prompt,
            "--yolo",
            "--output-format",
            "json",
        ]
    return [
        exe,
        "-p",
        prompt,
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]


def _ps_quote(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _windows_runtime_fallback_command(cmd: list[str]) -> Optional[list[str]]:
    if os.name != "nt" or not cmd:
        return None
    exe_name = Path(str(cmd[0] or "")).name.lower()
    runtime = ""
    if exe_name.startswith("gemini"):
        runtime = "gemini"
    elif exe_name.startswith("claude"):
        runtime = "claude"
    if not runtime:
        return None
    args = " ".join(_ps_quote(str(a)) for a in cmd[1:])
    ps_cmd = f"$ErrorActionPreference='Stop'; & {runtime} {args}"
    return [
        "powershell",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        ps_cmd,
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
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if flags:
            popen_kwargs["creationflags"] = flags
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        fallback_cmd = _windows_runtime_fallback_command(cmd)
        if not fallback_cmd:
            raise
        print(f"[news] runtime launch fallback via powershell for: {cmd[0]}", flush=True)
        proc = subprocess.Popen(fallback_cmd, **popen_kwargs)
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
    mode: str = "news",
) -> list[str]:
    """Call runtime CLI with web search, return prefixed multi-brief items."""
    agent_mode = _normalize_agent_mode(mode)
    exclude_clause = ""
    if reported:
        # Include up to 12 recent headlines to avoid repeats.
        recent = [_strip_brief_prefix(s) for s in reported[-12:]]
        recent = [s for s in recent if s]
        if agent_mode == "horror":
            story_ctx = recent[-4:]
            if story_ctx:
                exclude_clause = (
                    "前情提要："
                    + " ".join(story_ctx)
                    + "。请在此基础上继续推进剧情并制造新悬念，不要重复前文。"
                )
        else:
            exclude_clause = "排除已报道内容：" + "；".join(recent) + "。"

    if agent_mode == "all":
        prompt = ALL_BRIEF_PROMPT_TEMPLATE.format(
            interests=interests,
            exclude_clause=exclude_clause,
        )
    else:
        mode_cfg = MODE_SETTINGS.get(agent_mode, MODE_SETTINGS["news"])
        tpl = str(mode_cfg.get("prompt") or NEWS_ONLY_PROMPT_TEMPLATE)
        prompt = tpl.format(
            interests=interests,
            exclude_clause=exclude_clause,
        )

    env = os.environ.copy()
    _ensure_runtime_path(env)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CLAUDECODE", None)
    env["NEWS_AGENT_MODE"] = agent_mode

    candidates = _runtime_candidates(_runtime(), env)
    if not candidates:
        print("[news] no runtime available (gemini/claude not found on PATH)", file=sys.stderr, flush=True)
        return []

    for runtime in candidates:
        try:
            cmd = _build_runtime_command(prompt, runtime, env)
            print(f"[news] fetching news for: {interests} (runtime={runtime})", flush=True)
            raw = _run_runtime_prompt(
                cmd=cmd,
                env=env,
                runtime=runtime,
                stop_check=stop_check,
            )
            if not raw:
                continue
            print(f"[news] raw response: {raw[:120]}", flush=True)
            if agent_mode == "all":
                return _parse_multi_brief_items(raw)

            mode_cfg = MODE_SETTINGS.get(agent_mode, MODE_SETTINGS["news"])
            prefix = str(mode_cfg.get("prefix") or NEWS_PREFIX)
            limit = int(mode_cfg.get("limit") or 3)
            parsed = _parse_news_items(raw)
            prefixed = [f"{prefix} {item}" for item in parsed[:limit]]
            return _dedupe_keep_order(prefixed)
        except FileNotFoundError:
            print(f"[news] runtime not found: {runtime}", file=sys.stderr, flush=True)
            continue
        except Exception as e:
            print(f"[news] {runtime} error: {e}", file=sys.stderr, flush=True)
            continue
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


def _split_longform_chunk(text: str, *, max_chars: int = 230) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    sentences = [
        seg.strip()
        for seg in re.split(r"(?<=[。！？；])\s*", normalized)
        if seg and seg.strip()
    ]
    if not sentences:
        return [normalized]
    out: list[str] = []
    current = ""
    for sent in sentences:
        if not current:
            current = sent
            continue
        if len(current) + len(sent) <= max_chars:
            current = f"{current}{sent}"
            continue
        out.append(current)
        current = sent
    if current:
        out.append(current)
    return out


def _normalize_longform_sections(values: list[object]) -> list[str]:
    out: list[str] = []
    for value in values:
        cleaned = _clean_item(str(value or ""))
        if not cleaned:
            continue
        chunks = _split_longform_chunk(cleaned, max_chars=230)
        for chunk in chunks:
            c = chunk.strip()
            if not c:
                continue
            if len(c) < 45 and out:
                out[-1] = f"{out[-1]} {c}".strip()
            else:
                out.append(c)
    deduped = _dedupe_keep_order([s for s in out if len(s) >= 28])
    return deduped[:16]


def _parse_longform_script(raw: str) -> tuple[str, list[str]]:
    text = str(raw or "").strip()
    if not text:
        return "", []
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    def parse_obj(obj: dict[str, object]) -> tuple[str, list[str]]:
        title = str(obj.get("title") or obj.get("topic") or "").strip()
        for key in ("sections", "paragraphs", "chunks", "items", "content", "script"):
            value = obj.get(key)
            if isinstance(value, list):
                sections = _normalize_longform_sections(value)
                if sections:
                    return title, sections
            if isinstance(value, str):
                parts = re.split(r"\n\s*\n+", value)
                sections = _normalize_longform_sections(parts)
                if sections:
                    return title, sections
        return title, []

    title = ""
    sections: list[str] = []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            title, sections = parse_obj(parsed)
        elif isinstance(parsed, list):
            sections = _normalize_longform_sections(parsed)
    except Exception:
        pass

    if not sections:
        parsed_obj = _extract_json_object(text)
        if parsed_obj:
            title, sections = parse_obj(parsed_obj)

    if not sections:
        parts = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(parts) <= 1:
            parts = [line.strip() for line in text.splitlines() if line.strip()]
        sections = _normalize_longform_sections(parts)

    if sections and len(sections) < 8:
        expanded: list[str] = []
        for section in sections:
            expanded.extend(_split_longform_chunk(section, max_chars=170))
        sections = _normalize_longform_sections(expanded)

    return title, sections


def _select_prepared_longform_script(interests: str) -> Optional[tuple[str, list[str]]]:
    needle = str(interests or "").strip().lower()
    if not needle:
        return None
    for script in PREPARED_LONGFORM_SCRIPTS.values():
        aliases = tuple(str(a).strip().lower() for a in script.get("aliases") or ())
        if not any(alias and alias in needle for alias in aliases):
            continue
        title = str(script.get("title") or "专题长文说明").strip()
        sections = _normalize_longform_sections(list(script.get("sections") or []))
        if sections:
            return title, sections
    return None


def _fetch_longform_script_once(
    interests: str,
    stop_check: Optional[Callable[[], bool]] = None,
) -> tuple[str, list[str]]:
    prepared = _select_prepared_longform_script(interests)
    if prepared:
        title, sections = prepared
        print(f"[news] using prepared longform script: {title}", flush=True)
        return title, sections

    env = os.environ.copy()
    _ensure_runtime_path(env)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CLAUDECODE", None)
    prompt = AI_LONG_ONCE_PROMPT_TEMPLATE.format(interests=interests)

    candidates = _runtime_candidates(_runtime(), env)
    for runtime in candidates:
        try:
            cmd = _build_runtime_command(prompt, runtime, env)
            print(f"[news] preparing longform script for: {interests} (runtime={runtime})", flush=True)
            raw = _run_runtime_prompt(
                cmd=cmd,
                env=env,
                runtime=runtime,
                stop_check=stop_check,
            )
            if not raw:
                continue
            title, sections = _parse_longform_script(raw)
            if sections:
                total_chars = sum(len(s) for s in sections)
                print(
                    f"[news] longform prepared: sections={len(sections)} chars={total_chars}",
                    flush=True,
                )
                if not title:
                    title = f"{interests}专题说明"
                return title, sections
        except FileNotFoundError:
            print(f"[news] runtime not found: {runtime}", file=sys.stderr, flush=True)
            continue
        except Exception as e:
            print(f"[news] prepare longform error: {e}", file=sys.stderr, flush=True)
            continue

    fallback = _select_prepared_longform_script("cccc,框架")
    if fallback:
        title, sections = fallback
        return title, sections
    return "AI专题说明", []


def _run_ai_longform_once(
    group_id: str,
    interests: str,
    stop_check: Callable[[], bool],
) -> None:
    title, sections = _fetch_longform_script_once(interests, stop_check=stop_check)
    if not sections:
        send_message(group_id, f"{AI_LONG_PREFIX} 暂时未准备好长文稿，请稍后重试。")
        return

    intro = f"{AI_LONG_PREFIX} 专题开始：{title}。正在装载预先准备的长稿音频，请稍候。"
    send_message(group_id, intro)
    print(f"[news] ai_long started: {title} ({len(sections)} sections)", flush=True)
    if _sleep_with_stop(3, stop_check):
        return

    clean_sections: list[str] = []
    for section in sections:
        t = _strip_brief_prefix(section)
        if t:
            clean_sections.append(t)
    if not clean_sections or stop_check():
        return

    # Send one prepared long-form payload so TTS can pre-synthesize before playback.
    script_text = f"{AI_LONG_PREFIX} {' '.join(clean_sections)}"
    send_message(group_id, script_text)
    print(
        f"[news] ai_long sent prepared full script (sections={len(clean_sections)} chars={len(script_text)})",
        flush=True,
    )


def _format_stream_item(
    item: str,
    *,
    agent_mode: str,
    horror_marked: bool,
) -> tuple[str, bool]:
    text = str(item or "").strip()
    if not text:
        return "", horror_marked

    if agent_mode == "horror":
        body = _strip_brief_prefix(text)
        if not body:
            return "", horror_marked
        if horror_marked:
            return body, True
        return f"{HORROR_PREFIX} {body}", True

    if any(text.startswith(prefix) for prefix in KNOWN_BRIEF_PREFIXES):
        return text, horror_marked
    return f"{NEWS_PREFIX} {text}", horror_marked


def _mode_from_item(item: str, *, default_mode: str) -> str:
    s = str(item or "").strip()
    if s.startswith(MARKET_PREFIX):
        return "market"
    if s.startswith(HORROR_PREFIX):
        return "horror"
    if s.startswith(AI_LONG_PREFIX) or s.startswith(AI_TECH_PREFIX):
        return "ai_long"
    if s.startswith(NEWS_PREFIX) or s.startswith(MORNING_PREFIX):
        return "news"
    return _normalize_agent_mode(default_mode)


def _estimate_item_delay(item: str, *, default_mode: str = "news") -> int:
    mode = _mode_from_item(item, default_mode=default_mode)
    cfg = MODE_SETTINGS.get(mode, MODE_SETTINGS["news"])
    compact = re.sub(r"\s+", "", _strip_brief_prefix(item))
    chars = len(compact)
    chars_per_sec = float(cfg.get("chars_per_sec") or 4.0)
    gap = float(cfg.get("gap") or 2.0)
    min_delay = int(cfg.get("min_delay") or MIN_ITEM_DELAY)
    max_delay = int(cfg.get("max_delay") or MAX_ITEM_DELAY)
    est = int(round(chars / max(1.0, chars_per_sec) + gap))
    return max(min_delay, min(max_delay, est))


def _sleep_with_stop(seconds: int, stop_check: Callable[[], bool]) -> bool:
    for _ in range(max(1, int(seconds))):
        if stop_check():
            return True
        time.sleep(1)
    return stop_check()


def start_agent(
    group_id: str,
    interests: str = "AI,科技,编程",
    mode: str = "news",
    **_kwargs: object,
) -> None:
    """Main loop — streams news items with concurrent prefetch until stopped."""
    interests_list = interests.strip()
    runtime = _runtime()
    agent_mode = _normalize_agent_mode(mode or _agent_mode())

    print(f"[news] agent started - group={group_id}", flush=True)
    print(f"[news] interests: {interests_list}", flush=True)
    print(f"[news] runtime: {runtime}", flush=True)
    print(f"[news] mode: {agent_mode}", flush=True)
    if agent_mode == "ai_long":
        print("[news] mode: prepared longform playback", flush=True)
    else:
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

    if agent_mode == "ai_long":
        _run_ai_longform_once(group_id, interests_list, stop_check)
        print("[news] agent stopped", flush=True)
        return

    next_fetch_at = 0.0
    fetch_future: Optional[Future[list[str]]] = None
    horror_marked = False

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="news-fetch") as pool:
        while not stop:
            now = time.time()

            # Start background fetch when due.
            if fetch_future is None and now >= next_fetch_at:
                snapshot = reported[-20:].copy()
                print("[news] fetching next batch...", flush=True)
                fetch_future = pool.submit(fetch_news_items, interests_list, snapshot, stop_check, agent_mode)

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
                text, horror_marked = _format_stream_item(
                    item,
                    agent_mode=agent_mode,
                    horror_marked=horror_marked,
                )
                if not text:
                    continue
                send_message(group_id, text)
                reported.append(text)

                if len(reported) > 80:
                    reported = reported[-50:]

                # If queue is running low, prefetch in parallel while user is listening.
                if fetch_future is None and len(pending) <= PREFETCH_LOW_WATERMARK:
                    snapshot = reported[-20:].copy()
                    print("[news] prefetching next batch...", flush=True)
                    fetch_future = pool.submit(fetch_news_items, interests_list, snapshot, stop_check, agent_mode)
                    next_fetch_at = time.time() + 1

                delay = _estimate_item_delay(text, default_mode=agent_mode)
                print(f"[news] sent: {text[:60]} (next in ~{delay}s)", flush=True)
                if _sleep_with_stop(delay, stop_check):
                    break
                continue

            # No pending items yet: tick and wait for fetch completion.
            if _sleep_with_stop(1, stop_check):
                break

    print("[news] agent stopped", flush=True)

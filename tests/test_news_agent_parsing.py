import os

from cccc.ports.news.agent import (
    AI_LONG_PREFIX,
    HORROR_PREFIX,
    MARKET_PREFIX,
    NEWS_PREFIX,
    _build_runtime_command,
    _clean_item,
    _format_stream_item,
    _parse_longform_script,
    _parse_multi_brief_items,
    _parse_news_items,
    _normalize_agent_mode,
    _select_prepared_longform_script,
)


def test_clean_item_strips_urls_and_markdown() -> None:
    raw = "[标题](https://example.com)（来源: https://foo.bar）**加粗** `代码`"
    assert _clean_item(raw) == "标题加粗 代码"


def test_parse_news_items_from_json_array() -> None:
    raw = '["苹果发布新款芯片性能大幅提升","OpenAI发布全新模型能力升级"]'
    items = _parse_news_items(raw)
    assert items == ["苹果发布新款芯片性能大幅提升", "OpenAI发布全新模型能力升级"]


def test_parse_news_items_from_fenced_json() -> None:
    raw = """```json
["AI芯片公司公布季度财报增长超预期", "多地推进智能驾驶测试道路扩容"]
```"""
    items = _parse_news_items(raw)
    assert items == ["AI芯片公司公布季度财报增长超预期", "多地推进智能驾驶测试道路扩容"]


def test_parse_news_items_from_gemini_json_wrapper() -> None:
    raw = '{"response":"[\\"AI公司发布新模型进入公测\\",\\"自动驾驶企业宣布城区试运营扩容\\"]"}'
    items = _parse_news_items(raw)
    assert items == ["AI公司发布新模型进入公测", "自动驾驶企业宣布城区试运营扩容"]


def test_parse_news_items_fallback_lines_and_dedup() -> None:
    raw = "1. 量子计算初创公司获得新一轮融资\n2. 量子计算初创公司获得新一轮融资\n3. 机器人公司发布新平台"
    items = _parse_news_items(raw)
    assert items == ["量子计算初创公司获得新一轮融资", "机器人公司发布新平台"]


def test_parse_multi_brief_items_from_json_object() -> None:
    raw = """
{
  "news": ["多地发布算力基础设施建设计划"],
  "market": ["A股三大指数震荡收涨，AI硬件板块走强"],
  "ai_long": ["新一代开源多模态模型提升视频理解能力并带来跨模态应用跃迁"]
}
"""
    items = _parse_multi_brief_items(raw)
    assert items == [
        f"{NEWS_PREFIX} 多地发布算力基础设施建设计划",
        f"{MARKET_PREFIX} A股三大指数震荡收涨，AI硬件板块走强",
        f"{AI_LONG_PREFIX} 新一代开源多模态模型提升视频理解能力并带来跨模态应用跃迁",
    ]


def test_parse_multi_brief_items_fallback_to_legacy_array() -> None:
    raw = '["OpenAI发布新模型能力升级","苹果发布新款芯片性能提升"]'
    items = _parse_multi_brief_items(raw)
    assert items == [
        f"{NEWS_PREFIX} OpenAI发布新模型能力升级",
        f"{NEWS_PREFIX} 苹果发布新款芯片性能提升",
    ]


def test_parse_longform_script_from_json_object() -> None:
    raw = """
{
  "title": "CCCC框架专题",
  "sections": [
    "CCCC 通过 daemon、kernel 与 ports 分层，把入口形态和核心协作能力解耦，便于长期维护与扩展。",
    "在消息流上，所有关键交互会进入统一 ledger，再按接收方路由到 actor，确保过程可追踪和可回放。"
  ]
}
"""
    title, sections = _parse_longform_script(raw)
    assert title == "CCCC框架专题"
    assert len(sections) >= 2
    assert "分层" in sections[0]


def test_select_prepared_longform_script_for_cccc() -> None:
    selected = _select_prepared_longform_script("CCCC,框架,多Agent")
    assert selected is not None
    title, sections = selected
    assert "CCCC" in title
    assert len(sections) >= 10


def test_build_runtime_command_gemini_direct_exec() -> None:
    cmd = _build_runtime_command("hello", "gemini", {"PATH": os.environ.get("PATH", "")})
    assert "-p" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "hello"
    assert "-m" in cmd
    m_idx = cmd.index("-m")
    assert bool(str(cmd[m_idx + 1]).strip())
    assert "gemini" in cmd[0].lower()
    assert "powershell" not in cmd
    assert "sh" not in cmd


def test_build_runtime_command_claude_direct_exec() -> None:
    cmd = _build_runtime_command("hello", "claude", {"PATH": os.environ.get("PATH", "")})
    assert cmd[1:3] == ["-p", "hello"]
    assert "claude" in cmd[0].lower()
    assert "powershell" not in cmd
    assert "sh" not in cmd


def test_normalize_horror_mode_aliases() -> None:
    assert _normalize_agent_mode("horror") == "horror"
    assert _normalize_agent_mode("horror_story") == "horror"
    assert _normalize_agent_mode("story") == "horror"


def test_format_stream_item_horror_only_first_has_prefix() -> None:
    first, marked = _format_stream_item(
        f"{HORROR_PREFIX} 电梯门在13层自己打开了。",
        agent_mode="horror",
        horror_marked=False,
    )
    assert first.startswith(HORROR_PREFIX)
    assert marked is True

    second, marked2 = _format_stream_item(
        f"{HORROR_PREFIX} 走廊尽头传来钥匙划墙声。",
        agent_mode="horror",
        horror_marked=marked,
    )
    assert marked2 is True
    assert not second.startswith(HORROR_PREFIX)


def test_format_stream_item_news_still_uses_prefix() -> None:
    text, marked = _format_stream_item(
        "某AI公司发布了新模型。",
        agent_mode="news",
        horror_marked=False,
    )
    assert text.startswith(NEWS_PREFIX)
    assert marked is False

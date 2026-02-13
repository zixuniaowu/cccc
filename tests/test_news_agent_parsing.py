from cccc.ports.news.agent import (
    AI_TECH_PREFIX,
    MARKET_PREFIX,
    NEWS_PREFIX,
    _clean_item,
    _parse_multi_brief_items,
    _parse_news_items,
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
  "ai_tech": ["新一代开源多模态模型提升视频理解能力"]
}
"""
    items = _parse_multi_brief_items(raw)
    assert items == [
        f"{NEWS_PREFIX} 多地发布算力基础设施建设计划",
        f"{MARKET_PREFIX} A股三大指数震荡收涨，AI硬件板块走强",
        f"{AI_TECH_PREFIX} 新一代开源多模态模型提升视频理解能力",
    ]


def test_parse_multi_brief_items_fallback_to_legacy_array() -> None:
    raw = '["OpenAI发布新模型能力升级","苹果发布新款芯片性能提升"]'
    items = _parse_multi_brief_items(raw)
    assert items == [
        f"{NEWS_PREFIX} OpenAI发布新模型能力升级",
        f"{NEWS_PREFIX} 苹果发布新款芯片性能提升",
    ]

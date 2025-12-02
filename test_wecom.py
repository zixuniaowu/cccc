#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeCom Bridge Test Script

Usage:
    python test_wecom.py
    python test_wecom.py --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
"""
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

def test_webhook(webhook_url: str):
    """Test WeCom webhook by sending a test message."""
    print(f"Testing webhook: {webhook_url[:50]}...")

    # Test message
    payload = {
        "msgtype": "markdown_v2",
        "markdown_v2": {
            "content": "## 🧪 CCCC 企业微信桥接测试\n\n这是一条测试消息。\n\n### 功能\n- ✅ Webhook 连接正常\n- ✅ Markdown 渲染正常\n- ✅ CCCC 企业微信桥接已就绪\n\n---\n*测试时间：2025-12-01*"
        }
    }

    try:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json; charset=utf-8'}
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            errcode = result.get('errcode', -1)
            errmsg = result.get('errmsg', 'unknown')

            if errcode == 0:
                print("✅ 测试成功！消息已发送到企业微信群。")
                print(f"   响应: {errmsg}")
                return True
            else:
                print(f"❌ 测试失败！")
                print(f"   错误码: {errcode}")
                print(f"   错误信息: {errmsg}")
                return False

    except urllib.error.HTTPError as e:
        print(f"❌ HTTP 错误: {e.code} {e.reason}")
        try:
            error_body = e.read().decode('utf-8')
            print(f"   详情: {error_body}")
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"❌ 异常: {type(e).__name__}: {e}")
        return False

def load_webhook_from_config():
    """Load webhook URL from wecom.yaml config file."""
    config_path = Path('.cccc/settings/wecom.yaml')

    if not config_path.exists():
        return None

    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            return config.get('webhook_url', '').strip() or None
    except Exception:
        # Fallback: simple line parser
        try:
            for line in config_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line.startswith('webhook_url:'):
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        url = parts[1].strip().strip('"\'')
                        if url and not url.startswith('#'):
                            return url
        except Exception:
            pass
    return None

def main():
    print("=" * 60)
    print("CCCC 企业微信桥接测试工具")
    print("=" * 60)
    print()

    # Get webhook URL
    webhook_url = None

    # Check command line argument
    if len(sys.argv) > 1:
        if sys.argv[1] in ['-h', '--help']:
            print("用法:")
            print("  python test_wecom.py")
            print("  python test_wecom.py --webhook <URL>")
            print()
            print("示例:")
            print('  python test_wecom.py --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"')
            return
        elif sys.argv[1] == '--webhook' and len(sys.argv) > 2:
            webhook_url = sys.argv[2]
            print(f"📌 使用命令行参数的 Webhook URL")
        else:
            webhook_url = sys.argv[1]
            print(f"📌 使用命令行参数的 Webhook URL")

    # Try to load from config file
    if not webhook_url:
        print("🔍 正在从 .cccc/settings/wecom.yaml 加载配置...")
        webhook_url = load_webhook_from_config()
        if webhook_url:
            print(f"📌 找到配置文件中的 Webhook URL")
        else:
            print("⚠️  配置文件中未找到 webhook_url")

    # Try environment variable
    if not webhook_url:
        import os
        webhook_url = os.environ.get('WECOM_WEBHOOK_URL', '').strip()
        if webhook_url:
            print(f"📌 使用环境变量 WECOM_WEBHOOK_URL")
        else:
            print("⚠️  环境变量 WECOM_WEBHOOK_URL 未设置")

    # Prompt user if still not found
    if not webhook_url:
        print()
        print("❌ 未找到 Webhook URL！")
        print()
        print("请通过以下方式之一提供 Webhook URL：")
        print("  1. 命令行参数: python test_wecom.py --webhook <URL>")
        print("  2. 配置文件: 编辑 .cccc/settings/wecom.yaml")
        print("  3. 环境变量: export WECOM_WEBHOOK_URL=<URL>")
        print()
        try:
            webhook_url = input("或者现在输入 Webhook URL（直接回车跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n已取消。")
            return

    if not webhook_url:
        print("\n❌ 无法获取 Webhook URL，测试终止。")
        return

    # Validate URL format
    if not webhook_url.startswith('https://qyapi.weixin.qq.com/'):
        print(f"\n⚠️  警告: Webhook URL 格式可能不正确")
        print(f"   预期格式: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...")
        print(f"   当前格式: {webhook_url[:80]}")
        print()

    # Run test
    print()
    print("-" * 60)
    print("开始测试...")
    print("-" * 60)
    print()

    success = test_webhook(webhook_url)

    print()
    print("-" * 60)
    if success:
        print("✅ 测试完成！企业微信桥接配置正确。")
        print()
        print("下一步:")
        print("  1. 启动桥接: cccc bridge wecom start")
        print("  2. 查看状态: cccc bridge wecom status")
        print("  3. 查看日志: cccc bridge wecom logs -f")
    else:
        print("❌ 测试失败！请检查 Webhook URL 是否正确。")
        print()
        print("常见问题:")
        print("  - errcode 93000: Webhook URL 中的 key 无效")
        print("  - HTTP 404: URL 路径错误")
        print("  - 连接超时: 网络问题或代理设置")
        print()
        print("获取帮助:")
        print("  - 查看文档: docs/WECOM_BRIDGE_GUIDE.md")
        print("  - GitHub: https://github.com/ChesterRa/cccc/issues")
    print("-" * 60)
    print()

if __name__ == '__main__':
    main()

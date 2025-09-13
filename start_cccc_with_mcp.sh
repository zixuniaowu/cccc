#!/bin/bash
# CCCC + MCP Servers 啟動腳本

set -e

# 檢查是否在 CCCC 項目目錄
if [ ! -f ".cccc/settings/cli_profiles.yaml" ]; then
    echo "❌ 請在 CCCC 項目根目錄運行此腳本"
    exit 1
fi

echo "🚀 啟動 CCCC 雙AI協作系統 + MCP Servers"

# 1. 加載 MCP 環境變量
if [ -f ".cccc/settings/mcp_env.sh" ]; then
    echo "📦 加載 MCP 環境變量..."
    source .cccc/settings/mcp_env.sh
else
    echo "⚠️  警告: MCP 環境變量文件不存在，某些 MCP 服務可能無法工作"
    echo "   請編輯 .cccc/settings/mcp_env.sh 並添加你的 API tokens"
fi

# 2. 檢查 MCP 配置
if [ ! -f ".claude/mcp.json" ]; then
    echo "⚠️  警告: MCP 配置文件不存在，將使用默認配置"
else
    echo "✅ MCP 配置文件已找到"
fi

# 3. 安裝必要的 MCP servers (如果尚未安裝)
echo "📥 檢查 MCP servers..."

# 檢查並安裝 Filesystem MCP server
npm list -g @modelcontextprotocol/server-filesystem >/dev/null 2>&1 || {
    echo "📦 安裝 Filesystem MCP server..."
    npm install -g @modelcontextprotocol/server-filesystem
}

# 其他 MCP servers 可選安裝（按需啟用）
echo "💡 注意：Figma 和 GitHub MCP servers 需要額外配置"
echo "   請查看 https://github.com/modelcontextprotocol/servers 獲取安裝指南"

# 4. 設置增強的 CLI 命令
export CLAUDE_I_CMD="claude --mcp-config $(pwd)/.claude/mcp.json"
export GEMINI_I_CMD="gemini"

echo "🔧 Claude Code CLI: $CLAUDE_I_CMD"
echo "🔧 Gemini CLI: $GEMINI_I_CMD"

# 5. 啟動 CCCC
echo "🎯 啟動 CCCC 雙AI協作系統..."
echo ""
echo "💡 可用的 MCP 工具:"
echo "   • Figma - 設計系統集成"
echo "   • GitHub - 代碼庫管理"
echo "   • Filesystem - 增強文件操作"
echo "   • Brave Search - 實時搜索"
echo "   • Slack - 團隊通信"
echo ""
echo "📱 通過 Telegram 發送消息測試系統:"
echo "   /both 使用 Figma MCP 工具分析我的設計系統"
echo ""

# 啟動主程序
echo "🎯 激活虛擬環境並啟動 CCCC..."
if [ -f "cccc/venv/bin/activate" ]; then
    source cccc/venv/bin/activate
    echo "✅ 虛擬環境已激活"
    cccc run
else
    echo "⚠️  虛擬環境未找到，使用已安裝的命令..."
    if [ -f "cccc/venv/bin/cccc" ]; then
        cccc/venv/bin/cccc run
    else
        echo "⚠️  cccc 命令未找到，嘗試 Python 直接啟動..."
        python3 cccc/cccc.py run
    fi
fi
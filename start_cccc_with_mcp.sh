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
npm list -g @modelcontextprotocol/server-figma >/dev/null 2>&1 || {
    echo "📦 安裝 Figma MCP server..."
    npm install -g @modelcontextprotocol/server-figma
}

npm list -g @modelcontextprotocol/server-filesystem >/dev/null 2>&1 || {
    echo "📦 安裝 Filesystem MCP server..."
    npm install -g @modelcontextprotocol/server-filesystem
}

npm list -g @modelcontextprotocol/server-github >/dev/null 2>&1 || {
    echo "📦 安裝 GitHub MCP server..."
    npm install -g @modelcontextprotocol/server-github
}

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
python3 .cccc/venv/bin/cccc run
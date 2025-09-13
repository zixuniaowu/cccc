# CCCC + MCP Servers 集成指南

## 🎯 概述

本指南說明如何在 CCCC 雙AI協作系統中集成 Model Context Protocol (MCP) servers，實現與外部服務的強大集成。

## 🏗️ 系統架構

```
用戶 (Telegram)
    ↓
CCCC Orchestrator
    ↓
┌─────────────────┬─────────────────┐
│ Claude Code CLI │   Gemini CLI    │
│   + MCP Tools   │   (Standard)    │
│                 │                 │
│ • Figma        │                 │
│ • GitHub       │                 │
│ • Filesystem   │                 │
│ • Brave Search │                 │
│ • Slack        │                 │
└─────────────────┴─────────────────┘
```

## 📦 已配置的 MCP Servers

### 1. Figma Server
- **功能**: 設計系統分析、組件提取、設計轉代碼
- **配置**: `@modelcontextprotocol/server-figma`
- **環境變量**: `FIGMA_PERSONAL_ACCESS_TOKEN`
- **用途**: 
  - 分析 Figma 設計文件
  - 提取組件規格
  - 生成設計系統文檔

### 2. GitHub Server  
- **功能**: 代碼庫管理、Issue處理、PR自動化
- **配置**: `@modelcontextprotocol/server-github`
- **環境變量**: `GITHUB_PERSONAL_ACCESS_TOKEN`
- **用途**:
  - 自動化代碼審查
  - Issue 管理和標記
  - PR 創建和合併

### 3. Filesystem Server
- **功能**: 增強的文件系統操作
- **配置**: `@modelcontextprotocol/server-filesystem`
- **用途**:
  - 高級文件搜索和分析
  - 項目結構理解
  - 大規模文件操作

### 4. Brave Search Server
- **功能**: 實時網絡搜索
- **配置**: `@modelcontextprotocol/server-brave-search`
- **環境變量**: `BRAVE_API_KEY`
- **用途**:
  - 技術調研
  - 最新信息獲取
  - API 文檔查詢

### 5. Slack Server
- **功能**: 團隊通信集成
- **配置**: `@modelcontextprotocol/server-slack`  
- **環境變量**: `SLACK_BOT_TOKEN`
- **用途**:
  - 自動化通知
  - 工作流集成
  - 團隊協作

## 🚀 使用方法

### 1. 配置 API Tokens

編輯 `.cccc/settings/mcp_env.sh`:

```bash
# Figma Personal Access Token
export FIGMA_PERSONAL_ACCESS_TOKEN="figd_..."

# GitHub Personal Access Token  
export GITHUB_PERSONAL_ACCESS_TOKEN="ghp_..."

# Brave Search API Key
export BRAVE_API_KEY="BSAxxxxx"

# Slack Bot Token
export SLACK_BOT_TOKEN="xoxb-..."
```

### 2. 啟動系統

```bash
# 使用 MCP 增強啟動腳本
./start_cccc_with_mcp.sh
```

### 3. 測試 MCP 功能

通過 Telegram 發送以下消息:

```
# 測試 Figma 集成
/a 使用 Figma MCP 工具分析我的設計系統，提取主要組件規格

# 測試 GitHub 集成  
/a 檢查我們的 GitHub 倉庫，總結最近的 PR 和 Issues

# 測試多工具協作
/both 使用 Figma 和 GitHub MCP 工具，實現設計到代碼的完整工作流
```

## 🔧 自定義 MCP Servers

### 添加新的 MCP Server

1. **更新配置文件** (`.claude/mcp.json`):

```json
{
  "mcpServers": {
    "your-server": {
      "command": "npx",
      "args": ["@your-org/mcp-server"],
      "env": {
        "YOUR_API_KEY": "your_token_here"
      }
    }
  }
}
```

2. **添加環境變量** (`.cccc/settings/mcp_env.sh`):

```bash
export YOUR_API_KEY="your_actual_token"
```

3. **更新系統提示** (在 `CLAUDE.md` 中添加工具描述)

### 支持的 MCP Server 類型

- **stdio**: 標準輸入輸出服務器
- **SSE**: 服務器發送事件
- **HTTP**: RESTful API 服務器

## 💡 最佳實踐

### 1. MCP 工具使用原則

- **組合使用**: 結合多個 MCP 工具創建強大的工作流
- **證據導向**: 所有 MCP 工具的輸出都應作為決策證據
- **協作分享**: 在雙AI間共享 MCP 工具的洞察
- **漸進式**: 從簡單的單一工具使用開始

### 2. 工作流示例

**設計系統實施工作流**:
1. Figma → 提取設計規格
2. GitHub → 檢查現有組件庫
3. Filesystem → 分析項目結構  
4. Slack → 通知團隊進展

**自動化代碼審查**:
1. GitHub → 獲取 PR 信息
2. Filesystem → 分析代碼變更
3. Brave Search → 查詢最佳實踐
4. GitHub → 提交審查評論

## ⚠️ 注意事項

### 1. 安全性
- 永遠不要將 API tokens 提交到代碼庫
- 使用環境變量管理敏感信息
- 定期輪換 API keys

### 2. 性能
- MCP 調用有網絡延遲，合理規劃工作流
- 某些 MCP servers 有速率限制
- 監控 MCP server 的健康狀態

### 3. 調試
- 使用 `claude mcp list` 檢查 MCP server 狀態
- 查看 CCCC ledger 了解 MCP 調用歷史
- 檢查 `.cccc/state/` 目錄下的日誌文件

## 🔄 系統升級

當添加新的 MCP servers 或更新現有配置時:

1. 停止 CCCC 系統 (Ctrl+C)
2. 更新配置文件
3. 重新啟動: `./start_cccc_with_mcp.sh`
4. 測試新功能

## 📚 相關資源

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [Claude Code MCP Integration](https://docs.anthropic.com/en/docs/claude-code/mcp-servers)
- [Available MCP Servers](https://github.com/modelcontextprotocol/servers)

---

現在你的 CCCC 系統已經具備了強大的 MCP 能力，可以與 Figma、GitHub、Slack 等外部服務深度集成，實現真正的 AI-powered 工作流自動化！
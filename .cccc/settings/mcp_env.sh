#!/bin/bash
# MCP Server 環境變量配置
# 請填入你的實際 API tokens

# Figma Personal Access Token
# 獲取方式: https://help.figma.com/hc/en-us/articles/8085703771159-Manage-personal-access-tokens
export FIGMA_PERSONAL_ACCESS_TOKEN="YOUR_FIGMA_TOKEN_HERE"

# GitHub Personal Access Token  
# 獲取方式: https://github.com/settings/tokens
export GITHUB_PERSONAL_ACCESS_TOKEN="YOUR_GITHUB_TOKEN_HERE"

# Brave Search API Key
# 獲取方式: https://api.search.brave.com/app/keys
export BRAVE_API_KEY="YOUR_BRAVE_API_KEY_HERE"

# Slack Bot Token
# 獲取方式: https://api.slack.com/apps
export SLACK_BOT_TOKEN="YOUR_SLACK_TOKEN_HERE"

echo "MCP 環境變量已加載"
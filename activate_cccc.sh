#!/bin/bash
# CCCC 虛擬環境激活腳本

echo "🔧 激活 CCCC 虛擬環境..."

# 檢查並激活虛擬環境
if [ -f "cccc/venv/bin/activate" ]; then
    source cccc/venv/bin/activate
    echo "✅ 虛擬環境已激活"
    echo "💡 現在可以使用以下命令："
    echo "   cccc run        - 啟動 CCCC 系統"
    echo "   cccc doctor     - 檢查環境"
    echo "   cccc bridge     - 管理 Telegram 橋接"
    echo ""
    echo "📌 要退出虛擬環境，輸入: deactivate"
else
    echo "❌ 虛擬環境未找到"
    echo "   請先運行: cd cccc && python3 -m venv venv && pip install -e ."
fi
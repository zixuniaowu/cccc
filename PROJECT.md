# CCCC 雙AI協作測試專案

## 專案目標
測試和驗證 CCCC 雙AI協作系統的功能，使用 Claude Code CLI 和 Gemini CLI 進行協同開發。

## 當前任務
- 驗證雙AI協作系統是否正常運作
- 測試 Claude Code CLI (Peer A) 和 Gemini CLI (Peer B) 之間的通信
- 探索協作開發模式

## 技術棧
- Python 3.12
- CCCC v0.2.7
- Claude Code CLI (Peer A)
- Gemini CLI v0.3.4 (Peer B)
- tmux

## 約束條件
- 保持代碼改動小而可逆（≤150行）
- 優先使用證據驅動的開發模式
- 所有改動必須通過測試驗證

## 成功標準
- 雙AI系統能夠成功啟動並通信
- 兩個AI能夠協作完成簡單任務
- 系統穩定運行無錯誤
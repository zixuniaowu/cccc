# 📋 下午繼續工作 - 快速啟動指南

## 🎯 剩餘工作 (預估5-10分鐘)

### 第1步：首次登錄 Gemini CLI (3分鐘)
```bash
cd /home/zixuniaowu/cccc
gemini
```
**會自動引導你：**
- 選擇UI主題 (用方向鍵選擇)
- 點擊瀏覽器登錄連結 
- 用Google帳號登錄授權
- 獲得免費使用額度 (60次/分鐘，1000次/天)

### 第2步：啟動雙AI協作系統 (2分鐘)
```bash
source cccc/venv/bin/activate
cccc run
```
**會開啟tmux界面：**
- 左側窗格：Claude Code CLI (Peer A)
- 右側窗格：Gemini CLI (Peer B)  
- 右下角：系統狀態面板

### 第3步：測試系統 (可選)
在任一窗格中輸入簡單指令測試AI回應，觀察雙AI如何協作。

## 🚨 如果遇到問題

### Gemini登錄失敗
- 檢查網絡連接
- 確保瀏覽器已登錄Google帳號
- 重新運行 `gemini` 命令

### CCCC啟動失敗  
- 檢查虛擬環境：`source cccc/venv/bin/activate`
- 檢查狀態：`cccc doctor`
- 清理並重試：`cccc clean && cccc run`

## 📞 技術支持信息
- **文檔位置**: `/home/zixuniaowu/cccc/PROGRESS.md`
- **配置目錄**: `/home/zixuniaowu/cccc/.cccc/settings/`
- **系統提示**: `/home/zixuniaowu/cccc/CLAUDE.md` & `GEMINI.md`

---
**創建時間**: 2025-01-14 上午
**狀態**: 準備就緒，僅需登錄即可使用 ✅
# CCCC 语音 Agent 系统状态

> 最后更新: 2026-02-11

## 项目概述

通过 CCCC 框架 + Claude Code CLI，实现了一个**语音驱动的 AI 编程助手**。用户可以通过浏览器语音对话，让 AI Agent（perA）执行实际的编程任务——写代码、创建文件、运行命令等。

### 架构图

```
浏览器 (Eyes 页面)
  ├── 语音识别 (Chrome SpeechRecognition)
  ├── TTS 播报 (Chrome SpeechSynthesis, 分段)
  └── 消息收发 → /api/v1/groups/{gid}/send
                        │
                        ▼
              CCCC Daemon (port 8848)
              ├── 消息路由 (inbox/ledger)
              └── Actor 管理 (perA = foreman)
                        │
                        ▼
              echo_poller.py (轮询桥接)
              ├── 每 1s 轮询 perA inbox
              ├── 拉取对话历史 (ledger)
              └── 调用 Claude Code CLI
                        │
                        ▼
              claude -p $prompt --dangerously-skip-permissions
              ├── 读/写文件
              ├── 运行命令
              └── 返回文字摘要 → 发送回 CCCC → 浏览器 TTS 播报
```

## 当前状态

### 已完成

- [x] **Eyes 页面** (`/ui/eyes`) — 全屏动画眼睛界面，支持语音交互
  - 瞳孔跟随鼠标/面部追踪 (MediaPipe FaceLandmarker)
  - 情绪状态动画 (idle/listening/thinking/speaking/error)
  - 语音识别 (Chrome SpeechRecognition, continuous mode)
  - TTS 播报 (分段 ≤150 字，避免 Chrome 截断 bug)
  - 自动聆听 + keepalive 机制
- [x] **Agent 桥接** (`scripts/echo_poller.py`)
  - 轮询 inbox → 调用 Claude CLI → 发送回复
  - 对话历史上下文 (从 ledger 拉取最近 6 条)
  - UTF-8 编码兼容 (Windows cp932 问题已解决)
  - 工具调用正常 (文件读写、命令执行)
- [x] **perA 能写代码** — 已验证可以创建 `web/public/pinball.html` 弹珠游戏

### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| CCCC Daemon | `http://127.0.0.1:8848` | 后端 API |
| Vite Dev Server | `http://localhost:5173/ui/` | 前端开发服务器 |
| Eyes 页面 | `http://localhost:5173/ui/eyes` | 语音交互入口 |
| GROUP_ID | `g_878b8bbd4747` | 默认工作组 |
| ACTOR_ID | `perA` | Agent 身份 (foreman 角色) |
| Claude 超时 | 120s | 单次调用最长等待 |
| 历史条数 | 6 条 | 对话上下文窗口 |
| TTS 分段 | ≤150 字/段 | 避免 Chrome 截断 |

## 如何启动

### 前置条件

- Python 3.13+ (已安装 `requests`)
- Node.js + npm (已安装依赖)
- Claude Code CLI (`claude` 命令可用，路径: `C:\Users\zixun\AppData\Roaming\npm`)
- CCCC 后端已编译

### 启动步骤

**1. 启动 CCCC Daemon**

```bash
cd C:/Users/zixun/dev/cccc
python -m cccc.daemon
# 或者已编译的方式启动，监听 port 8848
```

**2. 启动 Vite 开发服务器**

```bash
cd C:/Users/zixun/dev/cccc/web
npm run dev
# 监听 port 5173，代理 /api → 127.0.0.1:8848
```

**3. 启动 Agent 轮询器**

```bash
cd C:/Users/zixun/dev/cccc
PYTHONUTF8=1 python scripts/echo_poller.py
```

> **重要**: 必须设置 `PYTHONUTF8=1` 环境变量，否则中文会因 cp932 编码报错。
> 在 Git Bash 中用 `PYTHONUTF8=1 python ...` (Unix 风格)，不要用 `set PYTHONUTF8=1` (那是 cmd.exe 语法)。

**4. 打开浏览器**

访问 `http://localhost:5173/ui/eyes`，点击麦克风按钮开始语音对话。

### 快速测试 (不用语音)

```bash
# 发送测试消息
PYTHONUTF8=1 python scripts/send_test.py

# 或者手动发送
PYTHONUTF8=1 python -c "
import requests
requests.post('http://127.0.0.1:8848/api/v1/groups/g_878b8bbd4747/send',
    json={'text': '你好', 'by': 'user', 'to': ['@foreman'], 'priority': 'normal'})
"
```

## 文件清单

| 文件 | 说明 | 状态 |
|------|------|------|
| `scripts/echo_poller.py` | Agent 轮询桥接脚本 | 核心，未提交 |
| `scripts/echo_agent.py` | 简单 echo 回复脚本 (旧) | 已弃用 |
| `scripts/send_test.py` | 发送测试消息的辅助脚本 | 工具 |
| `web/src/pages/TelepresenceEyes.tsx` | Eyes 语音交互页面 | 核心，未提交 |
| `web/src/index.css` | 眼睛动画样式 | 修改，未提交 |
| `web/src/main.tsx` | 入口，Eyes 路由判断 | 修改，未提交 |
| `web/public/pinball.html` | perA 创建的弹珠游戏 | Agent 产出 |
| `web/public/mediapipe/` | MediaPipe 面部追踪模型 | 依赖资源 |

## TODO

### 高优先级

- [ ] **语音识别断句问题** — 用户反馈"读到一半就断了"，continuous mode 下 Chrome 有时提前截断长句
- [ ] **自动聆听可靠性** — 添加了 keepalive 但仍需验证稳定性，可能需要处理 Chrome 权限在 HMR 后丢失的问题
- [ ] **Agent 响应速度** — 编程任务需要 60-120s，考虑：
  - 先发一条"正在处理..."的即时回复
  - 完成后再发实际结果
- [ ] **提交代码** — 当前所有改动都未 git commit

### 中优先级

- [ ] **持久化会话模式** — 当前 `claude -p` 是一次性调用，每次都重新启动。升级为 `--input-format stream-json` 持久进程可以：
  - 保持对话上下文 (不依赖 ledger 历史)
  - 更快的响应 (无启动开销)
  - 更好的工具链使用
- [ ] **多 Agent 协作** — 目前只有 perA 在工作，echo/boss/gemini 都已禁用。可以启用多个 Agent 分工
- [ ] **TTS 语音质量** — Chrome 内置 TTS 音质一般，考虑接入更好的 TTS 服务 (Azure/Google TTS API)

### 低优先级

- [ ] **面部追踪优化** — MediaPipe FaceLandmarker 在某些光线下不稳定
- [ ] **移动端适配** — Eyes 页面在手机上的交互体验
- [ ] **Agent 能力边界提示** — 当任务超出能力范围时给出明确反馈，而不是超时无响应
- [ ] **对话历史持久化** — 目前 ledger 历史有限，长对话上下文会丢失
- [ ] **错误恢复** — poller 崩溃后的自动重启机制 (systemd/pm2 等)

## 已知问题

1. **Windows 编码 (cp932)** — 已通过 `PYTHONUTF8=1` + PowerShell UTF-8 读取解决，但如果忘记设置环境变量会立即崩溃
2. **Chrome TTS 截断** — 已通过分段发音解决，但分段边界可能不自然
3. **Headless Runner 不启动进程** — Windows 上 CCCC 的 headless runner 不 spawn CLI 进程，必须靠 echo_poller.py 外部桥接
4. **HMR 后需重新授权麦克风** — Vite 热更新后 Chrome 可能需要用户重新点击才能启用语音识别

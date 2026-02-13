# CCCC 语音 Agent 系统状态

> 最后更新: 2026-02-14

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

## 当前技术栈（2026-02-14 增量整理）

> 这一节是对 2026-02-11 版本报告的补充，聚焦“现在正在使用”的实现与依赖。

### 1) 前端（Eyes 页面）

| 类别 | 当前实现 | 关键位置 |
|------|----------|----------|
| UI 框架 | React 18 + TypeScript + Vite 6 | `web/package.json` |
| 状态管理 | React Hooks + `zustand`（全局状态场景） | `web/package.json` |
| 样式 | CSS + Tailwind 工具链（构建） | `web/package.json`, `web/src/index.css` |
| 实时消息 | SSE (`EventSource`) + 失败后轮询回退（3.5s） | `web/src/pages/eyes/useSSEMessages.ts` |
| 语音识别 | `SpeechRecognition/webkitSpeechRecognition`（continuous + interim + watchdog） | `web/src/pages/eyes/useSpeechRecognition.ts` |
| 语音播报 | `speechSynthesis`（分段播报 + watchdog + 可主动 cancel） | `web/src/pages/eyes/useTTS.ts` |
| 视觉识别 | MediaPipe Tasks Vision：Face + Hand + Pose（GPU/CPU 回退） | `web/src/pages/eyes/useEyeTracking.ts` |
| 屏幕观察 | `getDisplayMedia` 截屏 + JPEG 上传给 Agent 分析 | `web/src/pages/eyes/useScreenCapture.ts` |
| 本地偏好 | `localStorage` 保存 voice/autoListen/cameraPreview/截屏参数 | `web/src/pages/eyes/usePreferences.ts` |

### 2) 视觉识别模型与资源

| 资源 | 用途 | 路径 |
|------|------|------|
| `face_landmarker.task` | 脸部 mesh 与视线估计输入 | `web/public/mediapipe/face_landmarker.task` |
| `hand_landmarker.task` | 手部关键点识别（最多 2 手） | `web/public/mediapipe/hand_landmarker.task` |
| `pose_landmarker_lite.task` | 身体姿态关键点识别 | `web/public/mediapipe/pose_landmarker_lite.task` |
| `vision_wasm*.{js,wasm}` | MediaPipe wasm runtime | `web/public/mediapipe/wasm/` |

### 3) 后端与通信层

| 类别 | 当前实现 | 关键位置 |
|------|----------|----------|
| Web/API | FastAPI + Uvicorn + Pydantic | `pyproject.toml`, `src/cccc/ports/web/app.py` |
| 协作内核 | CCCC Daemon（group / inbox / ledger / actor 路由） | `src/cccc/daemon/server.py` |
| 消息推送 | `/api/v1/groups/{gid}/ledger/stream`（SSE） | `src/cccc/ports/web/app.py` |
| 新闻控制 API | `/api/news/status` `/api/news/start` `/api/news/stop` | `src/cccc/ports/web/app.py` |

### 4) 新闻播报链路（现状）

| 项目 | 当前实现 | 关键位置 |
|------|----------|----------|
| Agent 入口 | `python -m cccc.ports.news` | `src/cccc/ports/news/__main__.py` |
| LLM 运行时 | 默认 `gemini`，可切 `claude` | `src/cccc/ports/news/agent.py` |
| 内容结构 | 三栏目 JSON：`news` / `market` / `ai_tech` | `src/cccc/ports/news/agent.py` |
| 播报前缀 | `[新闻简报]` `[股市简报]` `[AI新技术说明]` | `src/cccc/ports/news/agent.py`, `web/src/pages/eyes/constants.ts` |
| 默认兴趣词 | `AI,科技,编程,股市,美股,A股` | `src/cccc/ports/news/__main__.py`, `web/src/services/api.ts` |

### 5) 最近稳定性修复（已落地）

- [x] `停止新闻播报` 增强：不再只依赖 `news_agent.pid`，会扫描并终止孤儿 `cccc.ports.news` 进程（Windows/Linux 都有兜底逻辑）。
  - 位置: `src/cccc/ports/web/app.py`
- [x] Eyes 页点击“停止新闻播报”时，立即清空并取消 TTS 队列，避免“已经停了但还在念”。
  - 位置: `web/src/pages/eyes/index.tsx`
- [x] 新闻播报期间保持当前焦点/视图，不强制跳转到新文字区域。
  - 位置: `web/src/pages/eyes/index.tsx`

### 6) 当前 Eyes 源码结构（替代旧版单文件说明）

| 文件 | 作用 |
|------|------|
| `web/src/pages/eyes/index.tsx` | Eyes 页面主编排（消息、TTS、新闻开关、布局） |
| `web/src/pages/eyes/useEyeTracking.ts` | 摄像头 + Face/Hand/Pose 识别与覆盖层绘制 |
| `web/src/pages/eyes/useSpeechRecognition.ts` | 语音识别生命周期与自动重启 |
| `web/src/pages/eyes/useTTS.ts` | TTS 分段播报、进度、取消 |
| `web/src/pages/eyes/useSSEMessages.ts` | SSE 监听与轮询降级 |
| `web/src/pages/eyes/useScreenCapture.ts` | 屏幕截图上传给 Agent 分析 |
| `web/src/pages/eyes/usePreferences.ts` | 本地偏好持久化 |
| `web/src/pages/eyes/MobileCompanionLayout.tsx` | 移动端伴随布局 |
| `web/src/services/api.ts` | Eyes 相关 API 封装（含 news start/stop/status） |

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
| `src/cccc/ports/web/app.py` | Web API、SSE、news start/stop/status | 核心 |
| `src/cccc/ports/news/agent.py` | 新闻抓取与分栏目播报逻辑 | 核心 |
| `src/cccc/ports/news/__main__.py` | 新闻 Agent 模块入口 | 入口 |
| `web/src/pages/eyes/index.tsx` | Eyes 页面主编排 | 核心 |
| `web/src/pages/eyes/useEyeTracking.ts` | Face/Hand/Pose 识别与网格渲染 | 核心 |
| `web/src/pages/eyes/useSpeechRecognition.ts` | 浏览器语音识别控制 | 核心 |
| `web/src/pages/eyes/useTTS.ts` | 分段 TTS 与播报取消 | 核心 |
| `web/src/pages/eyes/useSSEMessages.ts` | SSE + 轮询降级 | 核心 |
| `web/src/pages/eyes/useScreenCapture.ts` | 桌面截图上传分析 | 功能模块 |
| `web/src/services/api.ts` | 前端 API 封装（含新闻接口） | 基础模块 |
| `web/public/pinball.html` | Agent 产出示例页面 | 示例 |
| `web/public/mediapipe/` | MediaPipe task 与 wasm 资源 | 依赖资源 |

## TODO

### 高优先级

- [ ] **语音识别断句问题** — 用户反馈"读到一半就断了"，continuous mode 下 Chrome 有时提前截断长句
- [ ] **自动聆听可靠性** — 添加了 keepalive 但仍需验证稳定性，可能需要处理 Chrome 权限在 HMR 后丢失的问题
- [ ] **Agent 响应速度** — 编程任务需要 60-120s，考虑：
  - 先发一条"正在处理..."的即时回复
  - 完成后再发实际结果

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

# AI 语音伴侣 - Telepresence Eyes

一对会动的眼睛，随时跟你对话。  
支持桌面端与手机端协同，具备语音对话、摄像头跟随、桌面观察、新闻播报等能力。

![AI Eyes](docs/screenshots/eyes-closeup.png)

## 项目定位

本项目基于 `CCCC` 多智能体协作内核，提供一个浏览器端 AI 伴侣界面：

- 桌面端：完整控制台（语音、摄像头、新闻、桌面观察）
- 手机端：全屏陪伴模式（轻交互、可随身携带）
- 同工作组：桌面和手机共享会话上下文

## 核心功能

- 语音对话：浏览器语音识别 + TTS 语音播报
- 眼睛动画：Canvas2D 实时渲染（虹膜、瞳孔、眨眼、情绪状态）
- 面部跟随：摄像头追踪视线方向
- 桌面观察：定时截图并交由 AI 分析
- 新闻播报：按主题定时抓取并生成摘要语音

## 界面预览

![Desktop View](docs/screenshots/desktop-viewport.png)
![Desktop QR](docs/screenshots/desktop-qr.png)
![GitHub Preview](docs/github-preview.png)
![Report Preview](docs/report-preview.png)

## 运行要求

- Python `3.11`（最低 `3.9`）
- Node.js `18+`（建议 `20+`）
- npm `9+`
- Windows / macOS / Linux
- 浏览器需允许麦克风与摄像头权限

## 快速开始（Windows）

1. 克隆项目

```powershell
git clone https://github.com/zixuniaowu/cccc.git
cd cccc
```

2. 安装后端依赖

```powershell
uv venv -p 3.11 .venv
uv pip install -e .
```

3. 安装前端依赖

```powershell
cd web
npm install
cd ..
```

4. 构建前端静态资源（写入 Python 包）

```powershell
cd web
npm run build
cd ..
```

5. 启动服务

```powershell
.venv\Scripts\python -m cccc.cli
```

6. 打开页面

- Web UI: `http://127.0.0.1:8848/ui/`

## 一键启动脚本

项目根目录提供 `start.ps1`：

```powershell
./start.ps1 -LocalHome
```

脚本会自动准备虚拟环境并拉起 daemon + web。

## 本地开发流程

### 后端开发

```powershell
uv venv -p 3.11 .venv
uv pip install -e .
uv run pytest
```

### 前端开发（热更新）

```powershell
cd web
npm install
npm run dev -- --host --base /ui/
```

说明：前端 dev server 会将 `/api` 代理到后端 `8848` 端口。

### 前端打包到后端

```powershell
cd web
npm run build
```

产物会更新到 `src/cccc/ports/web/dist`，用于打包与发布。

## 常用命令

```powershell
# 运行后端（等价入口）
.venv\Scripts\python -m cccc.cli
cccc

# 后端测试
uv run pytest

# 前端 lint / build
cd web
npm run lint
npm run build
```

## 环境变量

可通过环境变量调整服务行为：

- `CCCC_WEB_HOST`：Web 监听地址（默认 `127.0.0.1`）
- `CCCC_WEB_PORT`：Web 监听端口（默认 `8848`）
- `CCCC_WEB_LOG_LEVEL`：日志等级（如 `info`、`debug`）
- `CCCC_HOME`：运行时数据目录（默认用户主目录下）

如果你启用外部模型服务，请按所用 provider 配置对应 API Key。

## 目录结构

```text
src/cccc/                      # Python 内核、CLI、Web 适配层
src/cccc/ports/web/dist/       # 打包后的 Web 静态资源
web/                           # React + Vite 前端源码
web/public/                    # 静态资产（含页面与模型资源）
tests/                         # pytest 测试
scripts/                       # 本地脚本与自动化工具
docs/                          # 文档与截图
```

## 提交与发布约定

- 使用 Conventional Commits：`feat:` `fix:` `docs:` `chore:`
- 提交前建议至少执行：

```powershell
uv run pytest
cd web
npm run lint
npm run build
```

- `node_modules` 不入库，克隆后本地执行 `npm install` 即可
- 日志、临时文件、缓存文件不入库

## 常见问题

### 1) 为什么仓库里没有 `node_modules`？

`node_modules` 体积大且可再生，属于本地构建产物。  
正确流程是克隆后在 `web/` 目录执行 `npm install`。

### 2) 打开页面后麦克风/摄像头不可用

- 确认浏览器权限已允许
- 建议使用 `localhost` 或受信任的本地地址
- 检查是否被其他应用占用设备

### 3) 改了前端但页面没变化

- 开发模式使用 `npm run dev`
- 发布模式需重新执行 `npm run build`

## License

Apache-2.0

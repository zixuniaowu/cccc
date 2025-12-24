# CCCC vNext（重写进行中）

CCCC 正在重写为一个**全局多智能体交付内核**。

## vNext 形态

- **全局运行时目录**：`~/.cccc/`（working group / scopes / ledger / runtime state）
- **核心单位**：Working Group（类似群聊）+ **Project Root**（MVP：单一根目录；scopes 后置能力）
- **每个 group 一份追加式账本**：`~/.cccc/groups/<group_id>/ledger.jsonl`

## 快速开始（开发态）

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # 可选（默认就是 ~/.cccc）
cccc attach .
cccc groups
cccc send "hello"
cccc tail -n 20
cccc  # 启动 Web 控制台（等价于 `cccc web`）
```

打开 `http://127.0.0.1:8848/ui/`。

## Web UI（开发态）

```bash
# 终端 1：API/Web 端口（FastAPI）
cccc

# 终端 2：UI 开发服务器（Vite）
cd web
npm install
npm run dev
```

打开 `http://127.0.0.1:5173/ui/`（Vite），或直接使用 Python 端口在 `http://127.0.0.1:8848/ui/` 提供静态 UI。

## Web 终端

- Web UI 里每个 actor 有一个 `term` 按钮（xterm.js）。
- 终端会话由 daemon 管理的 **PTY runner** 承载（不需要 `tmux`；先启动 group/actor）。

规划文档：`docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

旧版 0.3.x 已以 tag `v0.3.28` 归档。

# CCCC vNext（リライト進行中）

CCCC は **グローバルなマルチエージェント配達カーネル**としてリライト中です。

## vNext モデル

- **グローバル実行ホーム**：`~/.cccc/`（working groups / scopes / ledger / runtime state）
- **中核ユニット**：Working Group（IM のグループのような単位）+ **Project Root**（MVP：単一ルート；scopes は後置）
- **グループごとの追記型 Ledger**：`~/.cccc/groups/<group_id>/ledger.jsonl`

## クイックスタート（開発）

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # 任意（デフォルトは ~/.cccc）
cccc attach .
cccc groups
cccc send "hello"
cccc tail -n 20
cccc  # Web コンソール起動（`cccc web` と同等）
```

`http://127.0.0.1:8848/ui/` を開きます。

## Web UI（開発）

```bash
# Terminal 1: API/Web port (FastAPI)
cccc

# Terminal 2: UI dev server (Vite)
cd web
npm install
npm run dev
```

`http://127.0.0.1:5173/ui/`（Vite）、または Python 側が `http://127.0.0.1:8848/ui/` で静的 UI を配信します。

## Web ターミナル

- Web UI の actor に `term` ボタン（xterm.js）があります。
- 端末セッションは daemon 管理の **PTY runner** が担当します（`tmux` 不要。group/actor を起動してから使います）。

計画ドキュメント：`docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

旧 0.3.x は tag `v0.3.28` にアーカイブされています。

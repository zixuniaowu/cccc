# CCCC 0.4.x（RC）— グローバル・マルチエージェント配達カーネル

[English](README.md) | [中文](README.zh-CN.md) | **日本語**

> ステータス：**0.4.0rc15**（Release Candidate）。UX と契約（contracts）を硬化中のため、破壊的変更の可能性があります。

CCCC は **local-first なマルチエージェント協調カーネル**です。成熟した IM に近い操作感を目指しつつ、以下で信頼性を担保します：

- 単一 writer の daemon（唯一の事実源）
- Working Group ごとの追記型 ledger（永続的な履歴）
- agent 向け MCP ツール面（「stdout で答えたが誰にも届かない」を避ける）

要点：

- 単一の daemon（`ccccd`）が複数の agent runtime（Claude Code / Codex CLI / Droid / OpenCode / Copilot など）を統合的に協調
- Working Group ごとに **追記型 ledger** を持ち、唯一の事実源にする
- **内蔵 Web UI** がコントロールプレーン（レスポンシブでモバイル優先）
- **組み込み MCP stdio server** により、agent がツール経由で CCCC を操作・送受信（「stdout に返したが誰も見ていない」を避ける）

旧 tmux/TUI 版（v0.3.x）： https://github.com/ChesterRa/cccc-tmux

---

## スクリーンショット

Chat UI：

![CCCC Chat UI](screenshots/chat.png)

Agent terminal UI：

![CCCC Agent Terminal](screenshots/terminal.png)

## なぜ v0.3 からリライト？

v0.3.x（tmux-first）は有効性を示しましたが、現実的な限界も明確でした：

1) **統一 ledger がない**  
   メッセージが複数のファイルに分散しており、配達のたびに agent がメッセージファイルを読み直す（または全文を取得する）必要が出やすく、効率が落ちる。

2) **actor 数の制約**  
   tmux レイアウトは 1〜2 actors に強く最適化されており、人数を増やすと UI/運用が破綻しやすい。

3) **agent がシステムを操作するための“完全な道具面”が弱い**  
   旧版は working group / actors / 設定などを agent がユーザー同様に管理できる統一ツール面が不足し、agent の自律的な計画・制御を引き出しにくい。

4) **リモートアクセスが一級の体験ではない**  
   tmux はローカルには強いが、スマホ/外出先からの継続利用には向かない。Web コントロールプレーンが必要。

0.4.x は境界を引き直しました：

- **統一 ledger**：各 group に追記型 ledger を持たせ、唯一の事実源にする。
- **N-actor モデル**：1 group で複数 actors を運用可能（add/start/stop/relaunch が一級操作）。
- **MCP コントロールプレーン**：agent がツールで CCCC を操作（メッセージ、context、actors、group state 等）。
- **Web-first コンソール**：成熟した Web 技術で構築。Cloudflare/Tailscale/WireGuard などと組み合わせれば高品質なリモート体験が可能。
- **IM グレードのメッセージ UX**：成熟した IM の操作感を取り込み、user↔agent のやり取りをチャット並みに簡単にする（@mention ルーティング、reply/quote、明示的な既読/確認、Web/IM 間で一貫）。
- **単一の runtime home**：`CCCC_HOME`（既定 `~/.cccc/`）に runtime state を集約。
- **単一 writer**：daemon が ledger の唯一の writer。ports は薄く保つ。

正直なトレードオフ：

- 0.4.x は daemon-based（ローカル常駐プロセス）。
- 0.4.x は RC。機能追加より「正しさ + UX 一貫性」を優先します。
- tmux ワークフローが好みなら `cccc-tmux`（v0.3.x）を使ってください。

---

## コア概念

- **Working Group**：協調単位（グループチャットのようなもの）+ 永続履歴 + 自動化。
- **Actor**：agent セッション（PTY / headless）。
- **Scope**：group に紐づくディレクトリ URL。各イベントは `scope_key` を持つ。
- **Ledger**：追記型イベントストリーム。メッセージと状態変更が一級のイベント。
- **`CCCC_HOME`**：グローバル runtime home（既定 `~/.cccc/`）。

既定の構造：

```text
~/.cccc/
  daemon/
    ccccd.addr.json   # daemon endpoint（クロスプラットフォーム；Windows は既定で TCP）
    ccccd.sock        # Unix domain socket（プラットフォーム/設定によってのみ存在）
    ccccd.log
  groups/<group_id>/
    group.yaml
    ledger.jsonl
    context/
    state/
```

---

## 要件

- Python 3.9+
- macOS / Linux / Windows（Windows ネイティブは `headless` runner 推奨。PTY が必要なら WSL）
- 対応する agent CLI を最低 1 つインストール（Claude/Codex/Droid/OpenCode/Copilot など）
- Node.js は **Web UI 開発** のみで必要（ユーザーは同梱 UI を利用可能）

---

## インストール

### TestPyPI から 0.4.x RC をインストール（現時点の推奨）

RC tag（例：`v0.4.0-rc15`）は **TestPyPI** に公開されます。依存は PyPI、RC パッケージのみ TestPyPI から取得します：

```bash
python -m pip install --index-url https://pypi.org/simple \
  --extra-index-url https://test.pypi.org/simple \
  cccc-pair==0.4.0rc15
```

注：現時点で PyPI の最新安定版は旧 v0.3.x 系です。0.4.x RC を試す場合は上記コマンドを使ってください。

### ソースからインストール（開発）

```bash
git clone https://github.com/ChesterRa/cccc
cd cccc
pip install -e .
```

Web UI 開発（任意）：

```bash
cd web
npm install
npm run dev
```

---

## クイックスタート（ローカル）

```bash
# 1) repo（scope）へ移動
cd /path/to/repo

# 2) Working Group を作成/紐付け
cccc attach .

# 3) runtime の MCP を設定（推奨）
cccc setup --runtime claude   # または codex / droid / opencode / copilot / ...

# 4) actors を追加（最初の enabled actor が foreman）
cccc actor add foreman --runtime claude
cccc actor add peer-1  --runtime codex

# 5) group を起動（enabled actors を spawn）
cccc group start

# 6) daemon + Web コンソール起動（Ctrl+C で両方停止）
cccc
```

`http://127.0.0.1:8848/` を開きます（`/ui/` にリダイレクト）。

---

## Runtimes と MCP 設定

CCCC は runtime 非依存ですが、MCP 設定方法は CLI によって異なります：

- MCP 自動設定：`claude`、`codex`、`droid`、`amp`、`auggie`、`neovate`、`gemini`
- MCP 手動設定（CCCC が手順を出力）：`cursor`、`kilocode`、`opencode`、`copilot`、`custom`

```bash
cccc runtime list --all
cccc setup --runtime <name>
```

推奨のデフォルト起動コマンド（actor 単位で上書き可能）：

- Claude Code：`claude --dangerously-skip-permissions`
- Codex CLI：`codex --dangerously-bypass-approvals-and-sandbox --search`
- Copilot CLI：`copilot --allow-all-tools --allow-all-paths`

---

## Web UI（モバイル優先）

内蔵 Web UI は主要なコントロールプレーンです：

- 複数 group のナビゲーション
- Actor 管理（add/start/stop/relaunch）
- Chat（@mention + reply）
- actor ごとの埋め込みターミナル（PTY runner）
- Context + Automation settings
- IM Bridge 設定
- PROJECT.md の表示/編集（repo root）

---

## PROJECT.md（プロジェクト憲法）

scope ルート（repo root）に `PROJECT.md` を置き、プロジェクトの憲法として扱います：

- agent は早い段階で読む（MCP ツール：`cccc_project_info`）。
- Web UI で表示/編集/作成できますが、ユーザーの明示指示がない限り agent は編集しません。

---

## IM Bridge（Telegram / Slack / Discord）

Working Group を IM にブリッジできます：

- 購読は明示的（例：チャットで `/subscribe` を送る）。
- 添付は `CCCC_HOME` の blobs に保存し、ledger には参照として記録（既定で repo には書き込みません）。

Web UI（Settings → IM Bridge）または CLI で設定：

```bash
cccc im set telegram --token-env TELEGRAM_BOT_TOKEN
cccc im start
```

---

## セキュリティ（リモート/スマホアクセス）

Web UI は高権限（actor 制御・プロジェクトファイルアクセスが可能）です。リモート公開する場合：

- `CCCC_WEB_TOKEN` を設定し、アクセスゲートウェイ（Cloudflare Access / Tailscale / WireGuard など）の背後に置く。
- 未認証のローカルポートをそのままインターネットに公開しない。

---

## CLI チートシート

```bash
cccc doctor
cccc runtime list --all
cccc groups
cccc use <group_id>

cccc send "hello" --to @all
cccc reply <event_id> "reply text"
cccc inbox --actor-id <id> --mark-read
cccc tail -n 50 -f

cccc daemon status|start|stop
cccc mcp
```

---

## Docs

- `docs/vnext/README.md`（入口）
- `docs/vnext/ARCHITECTURE.md`
- `docs/vnext/FEATURES.md`
- `docs/vnext/STATUS.md`
- `docs/vnext/RELEASE.md`（メンテナ向け）

## License

Apache-2.0

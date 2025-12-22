# CCCC vNext（リライト進行中）

CCCC は **グローバルなマルチエージェント配達カーネル**としてリライト中です。

## vNext モデル

- **グローバル実行ホーム**：`~/.cccc/`（working groups / scopes / ledger / runtime state）
- **中核ユニット**：Working Group（IM のグループのような単位）+ 複数 Scopes（ディレクトリ URL）
- **グループごとの追記型 Ledger**：`~/.cccc/groups/<group_id>/ledger.jsonl`

## クイックスタート（開発）

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # 任意（デフォルトは ~/.cccc）
cccc attach .
cccc groups
cccc send <group_id> "hello"
cccc tail <group_id> -n 20
```

計画ドキュメント：`docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

旧 0.3.x は tag `v0.3.28` にアーカイブされています。

# CCCC vNext (Rewrite in Progress)

CCCC is being rewritten into a **global multi-agent delivery kernel**.

## vNext Model

- **Global runtime home**: `~/.cccc/` (working groups, scopes, ledgers, runtime state)
- **Core unit**: Working Group (IM-like) with one or more Scopes (directory URLs)
- **One append-only ledger per group**: `~/.cccc/groups/<group_id>/ledger.jsonl`

## Quickstart (dev)

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # optional (default is ~/.cccc)
cccc attach .
cccc groups
cccc send <group_id> "hello"
cccc tail <group_id> -n 20
```

Planning doc: `docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

Legacy 0.3.x is tagged as `v0.3.28`.

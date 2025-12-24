# CCCC vNext (Rewrite in Progress)

CCCC is being rewritten into a **global multi-agent delivery kernel**.

## vNext Model

- **Global runtime home**: `~/.cccc/` (working groups, scopes, ledgers, runtime state)
- **Core unit**: Working Group (IM-like) with a **Project Root** (MVP: single root directory; scopes come later)
- **One append-only ledger per group**: `~/.cccc/groups/<group_id>/ledger.jsonl`

## Quickstart (dev)

```bash
pip install -e .
export CCCC_HOME=~/.cccc   # optional (default is ~/.cccc)
cccc attach .
cccc groups
cccc send "hello"
cccc tail -n 20
cccc  # start the web console (same as `cccc web`)
```

Open `http://127.0.0.1:8848/ui/`.

## Web UI (dev)

```bash
# Terminal 1: API port (FastAPI)
cccc

# Terminal 2: UI dev server (Vite)
cd web
npm install
npm run dev
```

Open the UI at `http://127.0.0.1:5173/ui/` (Vite), or build and let the Python port serve `web/dist` at `http://127.0.0.1:8848/ui/`.

## Web Terminal

- The Web UI includes an **xterm.js terminal**: click `term` on an actor.
- Terminal sessions are backed by the daemon-managed **PTY runner** (no `tmux` required; start the group/actor first).

Planning doc: `docs/vnext/CCCC_NEXT_GLOBAL_DAEMON.md`

Legacy 0.3.x is tagged as `v0.3.28`.

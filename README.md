# CCCC Pair - Multi-Peer Orchestrator for Evidence-First Delivery

Two always-on AI peers co-drive your repo as equals. They plan, build, critique, and converge by evidence - not by talk. You stay in control from tmux or your team chat.

:rocket: Not a chatbot UI. Not an IDE plugin. A production-minded orchestrator for long-running, real work.

## Why CCCC (pain -> payoff)

Single-agent pain (you may recognize these):
- :hourglass_flowing_sand: Stalls & restarts - context evaporates between runs; work drifts.
- :speech_balloon: Low-signal threads - long monologues, little verification, no audit trail.
- :triangular_flag_on_post: Decisions vanish - hard to see what changed and why.

CCCC payoff with two peers and repo-native anchors:
- :handshake: Multi-peer synergy - one builds, the other challenges; better options appear; errors die faster.
- :white_check_mark: Evidence-first loop - only tested/logged/committed results count as progress.
- :memo: POR/SUBPOR anchors - one strategic board (POR) + per-task sheets (SUBPOR) keep everyone aligned without ceremony.
- :bell: Low-noise cadence - built-in nudge/self-check trims chatter; the panel shows what matters, incl. "Next self-check" and "Next auto-compact".
- :mag: Auditable decisions - recent choices & pivots are captured; you can review and roll forward confidently.

## When to use CCCC
- You want autonomous progress you can trust, with small, reversible steps.
- You need collaboration you can observe in tmux/IM, not a black box.
- Your project benefits from a living strategic board and lightweight task sheets living in the repo.
- You care about repeatability: tests, stable logs, and commits as the final word.

## How it works (60 seconds)
- :link: One simple contract: peers write `<TO_USER>` / `<TO_PEER>` with a final fenced `insight` (who/kind/next/refs).
- :file_folder: Two anchors in your repo under `docs/por/`:
  - `POR.md` (strategic board): North-star, deliverables, roadmap (Now/Next/Later), risk radar, recent decisions/pivots, maintenance log.
  - `T######-slug/SUBPOR.md` (per-task sheet): goal/scope, 3-5 acceptance checks, cheapest probe, kill criteria, implementation notes, REV log, next step.
- :busts_in_silhouette: Optional Aux peer for big reviews or heavy lifting. Strategic notes sit in POR; tactical offloads sit in each SUBPOR.
- :tv: tmux UI: two panes (PeerA/PeerB) and a compact status panel. You will always see the POR path and "Next: self-check | auto-compact (policy)".

## Peers & Models (default choices, not a lock-in)

- :large_blue_circle: Peer A = Claude Code (default). We pick it for strong reasoning, careful code edits, and robust long sessions.
- :yellow_circle: Peer B = Codex CLI (default). We pick it for decisive implementation, fast iteration, and stable CLI behavior.
- :sparkles: Optional Aux = Gemini CLI (on-demand). We use it when a burst of non-interactive work helps (broad reviews, heavy tests, bulk transforms). Rationale: Gemini's long interactive sessions can be less stable in some setups, but it shines at short, structured jobs - perfect for an Aux you summon and dismiss.

Notes
- The two main peers collaborate as equals - both can think strategically and execute tactically. Aux is opt-in and task‑oriented.
- You can swap models at startup: a small roles wizard asks you to bind actors for PeerA, PeerB, and (optionally) Aux. Your choices are saved and reused next time; you can reconfigure on the next start.
- Aux is ON when you pick an actor for Aux; otherwise Aux is OFF (no runtime on/off toggles).
- CCCC is vendor‑agnostic; the orchestrator talks a simple mailbox contract.
- Strategy lives in `docs/por/POR.md`; execution details live in task SUBPORs - the peers update these naturally while working.

## What you get
- Small, reversible changes that ship continuously.
- A repo-native strategic board and per-task history you can actually read.
- IM bridges (Telegram/Slack/Discord) that bring the work to where your team already is.

---

## Requirements
- Python `>= 3.9`
- tmux (`brew install tmux` or `sudo apt install tmux`)
- git

Recommended CLIs
- :large_blue_circle: Peer A: Claude Code (default, available for Peer/Aux)
- :yellow_circle: Peer B: Codex CLI (default, available for Peer/Aux)
- :sparkles: Aux (optional): Gemini CLI (default, available for Peer/Aux)
- :robot: Also supported: Factory Droid (available for Peer/Aux)

## Install
- pipx (recommended)
  ```bash
  pipx install cccc-pair
  ```
- or venv
  ```bash
  python3 -m venv v && . v/bin/activate && pip install cccc-pair
  ```

## Quick Start (5 minutes)
1) Initialize in your repo
```bash
cccc init
```
2) Check environment
```bash
cccc doctor
```
3) (Optional) Connect Telegram
```bash
cccc token set  # paste your bot token (stored under .cccc/settings, gitignored)
```
4) Run orchestrator (tmux UI)
```bash
cccc run
```
- tmux opens: left/right = PeerA/PeerB, bottom-right = status panel.
- You can attach a bridge later with `cccc bridge ...` or set `autostart` in `.cccc/settings`.
5) Prepare your CLIs (one‑time)
- Put your project brief/scope in `PROJECT.md` (repo root). CCCC will weave it into the runtime SYSTEM so both peers align.
- Ensure the CLI commands for the actors you select are on PATH (e.g., `claude`, `codex`, `gemini`), or set env overrides like `CLAUDE_I_CMD` / `CODEX_I_CMD` if needed.
- On first run, the roles wizard will ask you to choose actors for PeerA/PeerB (and optionally Aux). You can reconfigure on the next run with the same wizard.
- Inspect current bindings anytime: `cccc roles`.

Aux (optional)
- Aux runs as a one‑off helper when you ask for it; there are no runtime on/off toggles.
- In tmux: run `/c <prompt>` or `c: <prompt>` to invoke Aux once.
- In chat bridges: use `/aux-cli "<prompt>"` to invoke Aux once.
- For a strategic review or external check, use `/review` to send a clear reminder bundle to both peers.

## A typical session (end-to-end, ~3 minutes)
1) Explore (short)
- In chat (or tmux), route an idea to both: `both: add a short section to README about team chat tips`.
- One peer frames intent; the other asks one focused question.
2) Decide (concise CLAIM)
- Write a CLAIM in `to_peer.md` with acceptance & constraints; the other peer COUNTERs with a sharper place or safer rollout.
3) Build (evidence-first)
- Propose a small, verifiable change with a 1-2 line EVIDENCE note (tests OK / stable logs / commit refs).
- Orchestrator logs outcomes to the ledger; the status panel updates.
4) Team visibility
- Telegram/Slack/Discord (optional) receive concise summaries; peers stay quiet unless blocked.

Cadence
- Every N handoffs (configurable), the orchestrator triggers a short self‑check to keep both peers aligned.
- PeerB also receives a "POR update requested …" reminder: review `POR.md` and all active `SUBPOR.md` (Goal/Acceptance/Probe/Kill/Next), align POR Now/Next with each SUBPOR Next, close/rescope stale items, ensure evidence/risks/decisions have recent refs, and check for gaps (create a new SUBPOR after peer ACK if needed).

## Folder layout (after `cccc init`)
```
.cccc/
  adapters/bridge_*.py            # chat bridges (optional)
  settings/                        # runtime profiles (tmux/bridges)
  mailbox/                         # to_user.md / to_peer.md; inbox/processed
  state/                           # ledger.jsonl, status/session (ephemeral)
  logs/                            # extra logs (ephemeral)
  orchestrator_tmux.py panel_status.py prompt_weaver.py ...

docs/
  por/
    POR.md                         # strategic board (North-star, deliverables, roadmap, risks, decisions)
    T000123-your-task/SUBPOR.md    # per-task sheet (goal/acceptance/probe/kill/impl/REV/next)
```

## POR/SUBPOR anchors (in your repo)
- `docs/por/POR.md` is the strategic board.
- `docs/por/T######-slug/SUBPOR.md` is a per-task sheet.
- Peers keep these brief and current as they work; you can read them any time.

## FAQ (short)
- **Does CCCC lock me into a UI?** No. tmux is default; IM bridges are optional and transport-agnostic.
- **Can I run only locally?** Yes. Everything works without any bridge.
- **What about safety?** Chats never change state. "Done" means tests/logs/commits. Irreversible changes are explicit and deliberate.

---

If you want to see a longer walkthrough or real-world examples, open an issue or start a discussion. We love shipping with teams who care about clarity, evidence, and taste.

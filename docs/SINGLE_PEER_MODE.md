# Single-Peer Mode Design Specification

> Version: 2.1
> Status: Planning
> Last Updated: 2024

## Overview

Single-peer mode allows CCCC to operate with only one agent (PeerA), providing:
- **Cost efficiency**: Eliminate token overhead from dual-peer communication
- **CCCC enhancements**: Foreman, Aux, self-check, auto-compact, Blueprint, IM bridges
- **Automation**: Foreman-driven periodic activation maintains autonomous operation

**Design Principle**: Single-peer mode is a **subset** of dual-peer mode, not a different paradigm. All existing CCCC mechanisms are preserved through minimal routing changes.

---

## Core Model: User + System as Composite Peer

### Dual-Peer Model

```
┌─────────┐              ┌─────────┐
│  PeerA  │ ←──────────→ │  PeerB  │    (AI ↔ AI)
└────┬────┘              └────┬────┘
     │                        │
     └──────────┬─────────────┘
                ↓
             User (occasional intervention)
```

### Single-Peer Model

```
┌─────────┐              ┌─────────────────────┐
│  Agent  │ ←──────────→ │   User + System     │    (AI ↔ Human+Machine)
└─────────┘              │  ├─ User: decisions │
                         │  └─ System: rhythm  │
                         └─────────────────────┘
```

**Key Insight**: In single-peer mode, User and System together form a "composite peer":
- **System**: Handles work rhythm (monitors progress, sends continuation prompts)
- **User**: Handles decisions (provides direction, answers questions)

---

## Communication Channels

### Channel Semantics

| Channel | Dual-Peer Recipient | Single-Peer Recipient | Purpose |
|---------|--------------------|-----------------------|---------|
| to_peer.md | Other AI Peer | System (rhythm keeper) | Work progress, Progress events |
| to_user.md | Human User | Human User (unchanged) | Summaries, questions, results |

### Information Flow

```
┌──────────────────────────────────────────────────────────┐
│                         Agent                             │
│                                                          │
│  to_peer.md ────────→ System (Rhythm Keeper)             │
│      │                 • Monitors Progress events        │
│      │                 • Triggers keepalive continuation │
│      │                 • Counts handoffs for self-check  │
│      │                                                   │
│  to_user.md ────────→ User (Decision Maker)              │
│                        • Reads summaries and results     │
│                        • Provides direction and input    │
│                        • Responds via TUI/IM             │
└──────────────────────────────────────────────────────────┘
```

### Why Both Channels Remain Meaningful

| Aspect | to_peer.md | to_user.md |
|--------|-----------|------------|
| Audience | Machine (System) | Human (User) |
| Content | Technical progress, handoff context | User-facing summaries |
| Processing | Automatic (keepalive trigger) | Manual (user reads) |
| Frequency | Every work step | Milestones, questions |

---

## Mechanism Reuse

### Keepalive (Unchanged Logic)

Current keepalive trigger condition:
```python
# keepalive.py - existing logic
def schedule_from_payload(sender_label: str, payload: str):
    if "<TO_PEER>" not in (payload or ""):
        return
    if not _has_progress_event(payload):
        return
    pending[sender_label] = {"due": time.time() + delay_s, ...}
```

**In single-peer mode**:
- Agent still writes TO_PEER with Progress events
- Same trigger condition applies
- Only the response routing changes (System sends Continue instead of delivering to PeerB)

### Self-Check (Unchanged Logic)

Self-check triggers after N "handoffs":
- **Dual-peer**: PeerA → PeerB counts as one handoff
- **Single-peer**: Agent → System → Agent counts as one handoff

The counting logic remains identical.

### Foreman, Aux, Blueprint (Unchanged)

These mechanisms operate independently of peer count:
- **Foreman**: Periodic external trigger to Agent inbox
- **Aux**: Helper agent invocation
- **Blueprint**: Task planning with progress markers

---

## Implementation

### Core Routing Change

The only significant code change is in message routing:

```python
def handle_to_peer_output(sender: str, payload: str):
    """Handle TO_PEER output from agent."""
    if is_single_peer_mode():
        # Single-peer: System intercepts, schedules continuation
        # Do NOT deliver to any peer inbox
        keepalive.schedule_from_payload(sender, payload)
        log_ledger({"kind": "single-peer-intercept", "from": sender})
    else:
        # Dual-peer: Deliver to the other peer's inbox
        other_peer = "PeerB" if sender == "PeerA" else "PeerA"
        deliver_to_inbox(other_peer, payload)
```

### Keepalive Response (Single-Peer)

When keepalive triggers in single-peer mode:

```python
def send_keepalive_continuation(peer_label: str):
    """Send continuation prompt to agent."""
    msg = """<FROM_SYSTEM>
Continue with your current task.

If task is complete, summarize results in to_user.md.
If blocked or need input, ask in to_user.md.
Otherwise, continue working and log progress in to_peer.md.
</FROM_SYSTEM>
"""
    write_to_inbox(peer_label, msg)
```

### Detection Function

```python
def is_single_peer_mode(roles_cfg: dict = None) -> bool:
    """Detect if running in single-peer mode."""
    if roles_cfg is None:
        roles_cfg = load_roles_config()
    peer_b = roles_cfg.get('peerB') or {}
    actor = peer_b.get('actor', '') if isinstance(peer_b, dict) else ''
    return not actor or actor.lower() == 'none'
```

---

## Configuration

### Enabling Single-Peer Mode

Set PeerB actor to "none" in TUI Setup or configuration:

```yaml
# settings/cli_profiles.yaml
roles:
  peerA:
    actor: claude-code
  peerB:
    actor: none          # ← Single-peer mode trigger
  aux:
    actor: claude-code   # Optional helper
```

### Keepalive Parameters

```yaml
# settings/cli_profiles.yaml
delivery:
  keepalive_delay_seconds: 60              # Dual-peer default

  single_peer:
    keepalive_delay_seconds: 240           # 4 minutes
    keepalive_max_nudges: 3                # Max continuation attempts
```

### Timing Design

```
Foreman interval:        ~900 seconds (15 minutes)
Single-peer keepalive:   3 × 240s = 720 seconds
Initial task execution:  ~180 seconds (estimate)
                         ────────────────────────
Total before Foreman:    ~900 seconds

→ Smooth handoff: keepalive handles short-term, Foreman handles long-term
```

### Exhaustion Behavior

When `keepalive_max_nudges` (3) is exhausted without new Progress:

1. **With Foreman**: Foreman's next periodic trigger (~15 min) will re-activate Agent
2. **Without Foreman**: Agent remains idle until User sends message via TUI/IM

This is intentional - if Agent stops producing Progress after 3 continuations, it's likely blocked or waiting for input. Foreman or User intervention is the appropriate response.

---

## tmux Layout

### Dual-Peer Layout

```
┌─────────────┬─────────────┐
│             │             │
│     TUI     │    PeerA    │
│             │             │
├─────────────┼─────────────┤
│             │             │
│     Log     │    PeerB    │
│             │             │
└─────────────┴─────────────┘
```

### Single-Peer Layout

```
┌─────────────┬─────────────────────────┐
│             │                         │
│     TUI     │                         │
│             │                         │
├─────────────┤         PeerA           │
│             │                         │
│     Log     │                         │
│             │                         │
└─────────────┴─────────────────────────┘
```

- Left side: Unchanged (TUI top, Log bottom)
- Right side: PeerA occupies full height

### Layout Selection

Layout is determined at **launch time** based on configuration:
- `orchestrator/tmux_layout.py` reads `is_single_peer_mode()` when creating panes
- No dynamic switching during session (restart required to change mode)

---

## System Prompt Changes

### Minimal Adjustments

The prompt changes are minimal since TO_PEER mechanism is preserved:

**Dual-Peer Prompt**:
```markdown
## Communication
- to_peer.md: Communicate with your peer for work handoff
- to_user.md: Communicate with the human user
```

**Single-Peer Prompt**:
```markdown
## Communication (Single-Peer Mode)

You have two output channels:

1. **to_peer.md** - Work Progress Log
   - Write work updates with Progress events
   - System monitors this to maintain execution rhythm
   - Include: Progress(tag=xxx): description of work done
   - This drives the continuation cycle

2. **to_user.md** - User Communication
   - Write user-facing summaries and questions
   - Use when you need human decision or input
   - Keep concise and actionable

Work rhythm:
- After writing to_peer.md with Progress, System will send continuation
- Continue working until task complete or blocked
- Report completion/blockers in to_user.md

### When to Write Which Channel

| Situation | Write to | Example |
|-----------|----------|---------|
| Completed a work step, more to do | to_peer.md | "Progress(tag=impl): Added auth middleware. Next: tests" |
| Reached milestone worth noting | Both | to_peer + brief summary in to_user |
| Task fully complete | to_user.md | "✓ Auth feature complete. 3 files changed, tests pass." |
| Blocked, need user decision | to_user.md | "Need decision: Use JWT or session cookies?" |
| Error/uncertainty | to_user.md | "Build failing on CI. Need guidance." |
```

### Self-Review Emphasis

Without peer review, add emphasis on self-review:

```markdown
## Self-Review (Important in Single-Peer Mode)

Without a peer to review your work:
- After significant steps, ask yourself: "Is this direction correct?"
- Before committing changes: "What could go wrong?"
- When uncertain: Report to user rather than proceed blindly
```

---

## TUI Changes

### Setup Panel Hint

When PeerB = "none" is selected:

```
┌─ PeerB ─────────────────────────────────────┐
│  ● none                                      │
│  ○ claude-code                               │
│  ○ codex                                     │
│                                              │
│  ℹ Single-peer mode: Only PeerA will run.   │
│    Foreman recommended for automation.       │
└──────────────────────────────────────────────┘
```

### Foreman Recommendation

When single-peer mode and Foreman = "none":

```
┌─ Foreman ───────────────────────────────────┐
│  ● none                                      │
│  ○ claude-code                               │
│                                              │
│  💡 Single-peer mode detected.               │
│     Enable Foreman for autonomous operation. │
└──────────────────────────────────────────────┘
```

### Command Handling

| Command | Single-Peer Behavior |
|---------|---------------------|
| `/a <text>` | Send to PeerA (normal) |
| `/b <text>` | Error: "Single-peer mode, use /a" |
| `/both <text>` | Equivalent to `/a` |

---

## Foreman in Single-Peer Mode

### Role Enhancement

Without peer-to-peer handoffs, Foreman becomes more important:

| Mode | Short-term Rhythm | Long-term Rhythm |
|------|-------------------|------------------|
| Dual-peer | Peer handoffs | Foreman (optional) |
| Single-peer | Keepalive continuation | Foreman (recommended) |

### With vs Without Foreman

**Single-Peer + Foreman (Autonomous Mode)**:
```
Agent works → TO_PEER → System continuation → Agent continues
                                    ↑
                              Foreman periodic trigger
```
Suitable for: Long projects, overnight runs, automation

**Single-Peer without Foreman (Interactive Mode)**:
```
User command → Agent works → TO_USER result → User next command
```
Suitable for: Interactive sessions, short tasks, learning

Both modes are valid; user chooses based on needs.

---

## Implementation Plan

### Phase 1: Core (Minimal Changes)

| Task | File | Change |
|------|------|--------|
| Detection | `common/config.py` | Add `is_single_peer_mode()` |
| Routing | `orchestrator/handoff.py` | Single-peer intercept branch |
| Config | `tui_ptk/app.py` | Allow PeerB = "none" |
| TUI hint | `tui_ptk/app.py` | Show single-peer message |

### Phase 2: Layout & Parameters

| Task | File | Change |
|------|------|--------|
| tmux layout | `orchestrator/tmux_layout.py` | 3-pane for single-peer |
| Keepalive params | `orchestrator/keepalive.py` | Single-peer delay/nudges |
| Skip PeerB start | `orchestrator_tmux.py` | Don't launch PeerB pane |

### Phase 3: Prompt & UX

| Task | File | Change |
|------|------|--------|
| Prompt variant | `prompt_weaver.py` | Single-peer communication section |
| /b command | `tui_ptk/app.py` | Friendly error |
| IM bridges | `adapters/bridge_*.py` | Handle /b, !b |
| Foreman hint | `tui_ptk/app.py` | Recommend when single-peer |

---

## Summary

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Core model | User+System as composite peer | Preserves all mechanisms |
| TO_PEER | Keep, System receives | Keepalive trigger unchanged |
| TO_USER | Unchanged | User communication unchanged |
| Code changes | Routing only | Minimal disruption |
| Prompt changes | Minimal | Agent behavior consistent |
| Foreman | Recommended, not required | User choice |

### What Changes vs Stays Same

| Aspect | Changes | Stays Same |
|--------|---------|------------|
| TO_PEER recipient | PeerB → System | Trigger logic, format |
| Keepalive | Parameters (delay, nudges) | Trigger condition |
| Self-check | Nothing | Count logic, cadence |
| Foreman | Importance (higher) | Mechanism |
| Aux | Nothing | Fully available |
| Blueprint | Nothing | Fully available |
| Prompt | Minor wording | Structure, TO_PEER/TO_USER |

### Value Proposition

```
Single-Peer CCCC = One Agent + Full Orchestration Infrastructure

Infrastructure preserved:
✓ Keepalive (rhythm maintenance)
✓ Self-check (quality cadence)
✓ Foreman (long-term automation)
✓ Aux (helper agent)
✓ Blueprint (task planning)
✓ Auto-compact (context management)
✓ IM bridges (remote access)

Cost saved:
✓ No dual-peer token overhead
✓ No peer-to-peer communication waste
```

This positions single-peer as "enhanced Claude Code with CCCC infrastructure" rather than "degraded dual-peer mode".

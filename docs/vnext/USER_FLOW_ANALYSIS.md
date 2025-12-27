# CCCC vNext — User Flow Analysis (Detailed Gap Analysis)

> This document walks through the complete user journey step-by-step, identifying gaps between current implementation and ideal user experience.
> 
> **Last Updated**: 2025-12-25 (Comprehensive review)

## Executive Summary

The system is now functionally complete for core workflows. Remaining gaps are mostly polish items.

**Completed in this session:**
- Group edit/delete (Web API + UI)
- Context editing (vision/sketch)
- @mention autocomplete
- Auto-start daemon when running `cccc`

---

## Phase 1: Installation & Verification

### Step 1.1: Install CCCC

```bash
pip install cccc-pair  # PyPI package name
# or
pip install -e .       # Development mode
```

**Status:**
- [x] pip install works
- [ ] **G1.1.1**: PyPI release not published yet

### Step 1.2: Verify installation

```bash
cccc version           # Show version
cccc doctor            # Check environment and runtimes
cccc runtime list      # List available agent CLIs (JSON)
```

**Status:**
- [x] `cccc version` works
- [x] `cccc doctor` works with runtime detection
- [x] `cccc runtime list` works

---

## Phase 2: First-Time Setup

### Step 2.1: Start CCCC

```bash
cccc                   # Opens Web UI, auto-starts daemon
```

**Status:**
- [x] Web UI opens at http://localhost:8848/ui/
- [x] Daemon auto-starts if not running
- [ ] **G2.1.1**: No first-time setup wizard

### Step 2.2: Setup MCP for agent runtime

```bash
cccc setup --runtime claude   # Configure MCP for Claude Code
cccc setup --runtime codex    # Configure MCP for Codex CLI
cccc setup --runtime droid    # Configure MCP for Droid
cccc setup --runtime opencode # Configure MCP for OpenCode
```

**Status:**
- [x] All 4 runtimes supported
- [x] Installs skill file + MCP config
- [ ] **G2.2.1**: No post-setup verification

---

## Phase 3: Working Group Management

### Step 3.1: Create working group

**Web UI:**
- Click "+ New" button in sidebar
- Modal opens with path input (required) and name input (auto-filled from directory)
- Enter project directory path, name auto-fills
- Click "Create Group"

**CLI:**
```bash
cccc group create --title "My Project"
cccc attach .  # Attach current directory
```

**Status:**
- [x] Group creation works (Web + CLI)
- [x] Empty state guidance in Web UI
- [x] Create Group Modal with path-first flow (like VS Code)
- [x] Auto-fill group name from directory name

### Step 3.2: Edit working group

**Web UI:**
- Click "edit" button next to group title
- Modal opens with title/topic fields
- Can delete group from modal

**Status:**
- [x] Group edit modal implemented
- [x] Group delete with confirmation
- [x] Topic displayed in header

### Step 3.3: Attach project scope

**Web UI:**
- Enter path in "Set project root path" input
- Click "Attach"
- Expand "Scopes" in header to see attached scopes

**CLI:**
```bash
cccc attach /path/to/project --group <group_id>
```

**Status:**
- [x] Attach works
- [x] Scope list display (expandable details)
- [x] Detach scope from UI (hover to show × button)

---

## Phase 4: Actor Management

### Step 4.1: Add actor

**Web UI:**
- Click "+ Add Actor" button in header
- Modal opens with role, runtime, runner, ID, command, and title fields
- Role selection: Foreman (★) or Peer
- Runtime dropdown shows availability (grays out unavailable)
- Runner selection: PTY (Terminal) or Headless (MCP)
- Actor ID auto-suggested based on runtime
- Command auto-filled based on runtime (editable)

**CLI:**
```bash
cccc actor add foreman --role foreman --runtime claude
cccc actor add peer-1 --role peer --runtime codex
cccc actor add custom --role peer --command "aider"
```

**Status:**
- [x] Add Actor Modal with all fields
- [x] Runtime availability detection
- [x] Actor ID auto-suggestion
- [x] Foreman uniqueness validation
- [x] Tooltips for role/runner
- [x] Runtime template auto-fill (command pre-filled, editable)

### Step 4.2: Start/stop actors

**Web UI:**
- Click actor badge to open dropdown menu
- Select "Start" or "Stop" from menu
- Or click "Start" button in header to start all actors

**CLI:**
```bash
cccc actor start <id>
cccc actor stop <id>
cccc group start  # Start all actors
cccc group stop   # Stop all actors
```

**Status:**
- [x] Individual actor start/stop via dropdown menu
- [x] Group start/stop
- [x] Status badge (enabled/disabled)
- [x] Actor dropdown menu with consolidated actions

### Step 4.3: Edit actor (换班)

**Web UI:**
- Stop actor first (edit button only visible when stopped)
- Click "edit" button on actor badge
- Edit modal: change runtime, command, title
- Command auto-filled when runtime changes (editable)
- Save and start with new configuration

**CLI:**
```bash
cccc actor stop foreman
cccc actor update foreman --runtime codex
cccc actor start foreman
```

**Status:**
- [x] Actor edit modal (only when stopped)
- [x] Runtime/command editing
- [x] Runtime template auto-fill in edit modal

### Step 4.4: Remove actor

**Web UI:**
- Click "remove" button on actor badge
- Confirmation dialog

**CLI:**
```bash
cccc actor remove <id>
```

**Status:**
- [x] Actor removal works

### Step 4.5: View actor terminal

**Web UI:**
- Click "term" button on actor badge (PTY runners only)
- Terminal modal opens with xterm.js
- Interrupt button (Ctrl+C)
- Focus button

**Status:**
- [x] Terminal modal works
- [x] Interrupt button
- [ ] **G4.5.1**: No terminal for headless runners (by design)

### Step 4.6: View actor inbox

**Web UI:**
- Click "inbox" button on actor badge
- Modal shows unread messages
- "Mark all read" button

**Status:**
- [x] Inbox modal works
- [x] Mark all read

---

## Phase 5: Messaging

### Step 5.1: Send message

**Web UI:**
- Type message in composer
- Select recipients via buttons or "To" input
- @mention autocomplete (type @ to trigger)
- Click "Send" or press Enter

**CLI:**
```bash
cccc send "Hello" --to @all
cccc send "Task for you" --to foreman
```

**Status:**
- [x] Message sending works
- [x] Recipient selection buttons
- [x] @mention autocomplete
- [x] Multi-line textarea input (Ctrl+Enter to send)
- [x] Read status display per recipient (✓ read / ○ unread)

### Step 5.2: Reply to message

**Web UI:**
- Click reply button (↩) on a message
- Reply bar shows quoted text
- Send reply

**CLI:**
```bash
cccc reply <event_id> "Reply text"
```

**Status:**
- [x] Reply with quote works
- [x] Quote text displayed

### Step 5.3: View message stream

**Web UI:**
- Event stream shows all messages
- Different styles for chat.message vs system.notify
- Auto-scroll to bottom

**Status:**
- [x] Event stream works
- [x] Event type differentiation
- [x] Visual distinction: user (green), agent (blue), system (amber)
- [x] Scroll to bottom button
- [ ] **G5.3.1**: No message search
- [ ] **G5.3.2**: No pagination/virtual scroll for large histories

---

## Phase 6: Context Management

### Step 6.1: View context

**Web UI:**
- Click "Context" button in header
- Panel shows vision, sketch, milestones, tasks, presence

**Status:**
- [x] Context panel implemented
- [x] All context types displayed

### Step 6.2: Edit context

**Web UI:**
- Click "edit" on Vision or Sketch
- Inline textarea for editing
- Save/Cancel buttons

**MCP Tools:**
```
cccc_context_sync: Batch update operations
cccc_vision_update: Update vision
cccc_sketch_update: Update sketch
cccc_milestone_*: Milestone management
cccc_task_*: Task management
```

**Status:**
- [x] Vision/Sketch editing in Web UI
- [x] All MCP tools implemented
- [ ] **G6.2.1**: No milestone/task editing in Web UI (MCP only)

---

## Phase 7: Foreman Autonomy

### Step 7.1: Foreman creates peers

**MCP Tools available to foreman:**
```
cccc_runtime_list: Discover available runtimes
cccc_actor_add: Create new peer
cccc_actor_start: Start peer
cccc_actor_stop: Stop peer
cccc_actor_remove: Remove peer
```

**SYSTEM prompt guidance:**
- Foreman receives detailed instructions on peer management
- Workflow: check runtimes → create peer → start → assign task

**Status:**
- [x] All MCP tools implemented
- [x] SYSTEM prompt guidance
- [x] Runtime detection for foreman

---

## Phase 8: Automation

### Step 8.1: NUDGE automation

- Daemon sends nudge when actor has unread messages for too long
- Nudge appears as system.notify event

**Status:**
- [x] NUDGE implemented
- [x] Settings panel to configure nudge interval

### Step 8.2: SELF-CHECK automation

- Daemon triggers self-check prompt periodically
- Helps agents reflect on progress

**Status:**
- [x] SELF-CHECK implemented
- [x] Settings panel to configure self-check interval

---

## Remaining Gaps Summary

### P1 (Important)

| ID | Description | Effort |
|----|-------------|--------|
| G5.3.1 | Message search | Medium |
| G5.3.2 | Virtual scroll for large histories | Medium |

### P2 (Nice to have)

| ID | Description | Effort |
|----|-------------|--------|
| G1.1.1 | PyPI release | Medium |
| G2.1.1 | First-time setup wizard | Large |

---

## New Gaps Identified (This Review)

### CLI Gaps

1. ~~**No `cccc status` command**~~ - ✅ Implemented
2. **No `cccc logs` command** - View daemon logs
3. **No `cccc config` command** - View/edit configuration

### Web UI Gaps

1. ~~**No scope list display**~~ - ✅ Implemented (expandable details)
2. ~~**No scope detach**~~ - ✅ Implemented (hover to show × button)
3. ~~**No group creation modal**~~ - ✅ Implemented (path-first flow like VS Code)
4. ~~**No actor edit**~~ - ✅ Implemented (stop → edit → start workflow)
5. ~~**No settings panel**~~ - ✅ Implemented (automation config)
6. ~~**No keyboard shortcuts**~~ - ✅ Implemented (Ctrl+Enter, Escape)
7. ~~**Add Actor inline form cluttered**~~ - ✅ Converted to modal
8. ~~**Actor buttons cluttered**~~ - ✅ Converted to dropdown menus

### Backend Gaps

1. ~~**No graceful shutdown**~~ - ✅ Implemented (SIGTERM/SIGINT handling)
2. ~~**No health check endpoint**~~ - ✅ Implemented (`/api/v1/health`)
3. **No metrics/stats** - Message count, actor uptime, etc.

---

## Next Actions

1. ~~Add scope list display in Web UI~~ ✅
2. ~~Add `cccc status` command~~ ✅
3. ~~Add health check endpoint~~ ✅
4. ~~Add keyboard shortcuts in Web UI~~ ✅
5. ~~Add actor edit modal~~ ✅
6. ~~Add read status display~~ ✅
7. ~~Add multi-line input~~ ✅
8. ~~Add scroll to bottom button~~ ✅
9. ~~Add settings panel (automation config)~~ ✅
10. ~~UI/UX Redesign: Create Group Modal~~ ✅
11. ~~UI/UX Redesign: Add Actor Modal~~ ✅
12. ~~UI/UX Redesign: Actor dropdown menus~~ ✅
13. Add message search/pagination
14. PyPI release preparation


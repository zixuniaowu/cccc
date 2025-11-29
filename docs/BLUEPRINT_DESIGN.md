# Task Structure Design Specification

> Version: 7.0
> Status: Planning
> Last Updated: 2024

## Overview

This document defines a structured task management system for CCCC that provides:
- **Upfront task planning** with known total count before execution
- **Progress Markers** — Agent sends one-line markers, Orchestrator updates files (reliable, low Agent burden)
- **Goal detection** with heuristics to know when to start planning
- **Smart threshold** with concrete rules (≤2 files, ≤50 lines → quick task)
- Clear progress tracking: "X of Y tasks complete"
- Support for both dual-peer and single-peer modes
- **System Prompt Specification** so Agents know exact protocol
- **Task Panel (TUI)** — distinctive expandable UI for task visibility

**Core insights:**
1. To know "how many tasks total", planning must happen BEFORE execution.
2. To ensure data accuracy, Orchestrator manages updates (not Agent direct file writes).

---

## Design Principles

### Command vs Natural Language

```
┌─────────────────────────────────────────────────────────────┐
│                    Design Principle                         │
│                                                             │
│  脚本能做的 → 命令实现（确定性、即时、零成本）                   │
│  需要智能的 → 自然语言（Agent处理）                           │
└─────────────────────────────────────────────────────────────┘
```

| 操作类型 | 实现方式 | 理由 |
|----------|----------|------|
| 数据查询/显示 | 命令 | 读文件、格式化，脚本即可 |
| 分析/判断/决策 | 自然语言 | 需要Agent智能 |
| 状态切换 | 命令 | 确定性操作 |
| 规划/重规划 | 自然语言 | 需要Agent理解和设计 |

**优点：**
- 效率：不浪费Agent token在简单查询上
- 可靠：命令是确定性的，无AI幻觉
- 速度：命令即时执行，无需等待Agent
- 成本：减少不必要的API调用

### Planning vs Execution: Why Different?

```
┌─────────────────────────────────────────────────────────────┐
│  Planning Phase: Agent 直接写文件                           │
│  - 创建 task.yaml (一次性结构定义)                          │
│  - 深思熟虑，不易遗漏                                       │
│  - Orchestrator 随后自动更新 scope.yaml                     │
├─────────────────────────────────────────────────────────────┤
│  Execution Phase: Agent 发 marker，Orchestrator 写文件      │
│  - 更新 step.status (频繁状态变化)                          │
│  - 容易遗忘，所以自动化                                     │
│  - 一行 marker 负担最小                                     │
└─────────────────────────────────────────────────────────────┘
```

**设计原理：**
- 规划是**深思熟虑的结构设计** → Agent 直接控制
- 执行是**频繁的状态更新** → 自动化以保证可靠性

---

## Goal Detection

### The Problem

Messages arrive in various forms. Not every message is a "goal" requiring planning:

```
"Add OAuth support"              ← Goal (new feature)
"What do you think about OAuth?" ← Question (not a goal)
"Fix the typo in README"         ← Quick task (skip planning)
"Continue from yesterday"        ← Resume (not a new goal)
"Also add logout button"         ← Follow-up (extend existing task)
```

### Detection Heuristics

Agent uses context + content to detect goals:

**Context signals:**
| Condition | Interpretation |
|-----------|----------------|
| No active tasks + substantive message | Likely a new goal |
| Active tasks exist + related topic | Likely a follow-up |
| Active tasks exist + unrelated topic | Possibly a new goal |
| Message routed to `both:` | Higher chance of being a goal |

**Content signals (Goal-like):**
- Action verbs: "add", "implement", "create", "build", "refactor", "migrate"
- Outcome descriptions: "users can...", "system should...", "enable..."
- Feature names: "OAuth", "dashboard", "notifications"
- Scope indicators: "module", "feature", "system"

**Content signals (Not a goal):**
- Questions: "what", "how", "why", "can you explain"
- References to existing work: "continue", "finish", "also"
- Single-file mentions: "fix the bug in auth.py"

### Agent Decision Flow

```
Message received
     ↓
┌─────────────────────────────────────────┐
│ Is this a goal requiring planning?      │
│                                         │
│ Check:                                  │
│ 1. Any active tasks? (context)          │
│ 2. Goal-like content signals?           │
│ 3. Complexity indicators?               │
│                                         │
│ ├── Clearly a goal → Threshold check    │
│ ├── Clearly not → Respond/execute       │
│ └── Uncertain → Ask ONE question        │
└─────────────────────────────────────────┘
```

### Uncertainty Handling

When uncertain, Agent asks ONE clarifying question:

```
User: "We need better error handling"

Agent: "I can approach this in two ways:
1. Quick fix: Add try-catch to the 3 API endpoints that are failing
2. Full implementation: Design an error handling system with logging,
   retry logic, and user-friendly messages

Which approach would you prefer?"
```

**Rule: Never ask "should I plan this?" — instead, offer concrete options.**

---

## The Fundamental Problem

Users need to answer:
- "本项目一共有多少任务？" → Requires a known denominator
- "现在大概进行到哪部分了？" → Requires X/Y format

Previous approach (SUBPOR, incremental task creation):
```
User goal → Agent creates tasks as it works → Total count unknown
                                               ↑
                                          永远不知道分母
```

**Solution: Planning Phase before Execution Phase**

```
User goal → Agent plans ALL tasks first → Then executes → Total count known
                    ↑
              关键改变：先规划后执行
```

---

## Complete Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                      Goal Received                          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Phase 0: Threshold Check                       │
│                                                             │
│  Agent judges: Is this a quick task?                        │
│  - Simple fix, single file, < 30 min estimated              │
│  - Type: bugfix, typo, config tweak, documentation          │
│                                                             │
│  ├── Yes → Execute directly (skip formal planning)         │
│  │         Record as "quick_task" in ledger                │
│  └── No  → Continue to Phase 1                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Phase 1: Planning                              │
│                                                             │
│  If goal is ambiguous:                                      │
│  - Agent explores codebase first                           │
│  - Identifies concrete sub-goals                           │
│  - Then creates task breakdown                             │
│                                                             │
│  Dual-Peer: PeerA plans → PeerB reviews (timeout: 10 min)  │
│  Single-Peer: Peer plans → Proceed immediately             │
│                                                             │
│  Output: All task.yaml files created (status: planned)     │
│  Output: scope.yaml created with initial count             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Phase 2: Execution                             │
│                                                             │
│  For each task (in order):                                 │
│  1. Activate task (planned → active)                       │
│  2. Execute steps sequentially                             │
│  3. Mark steps complete as criteria met                    │
│  4. When all steps done, mark task complete                │
│  5. Activate next task                                     │
│                                                             │
│  Mid-execution events:                                     │
│  - Scope expansion: Agent adds tasks, updates scope.yaml   │
│  - Blocked step: Mark blocked, notify, continue if possible│
│  - Replan: User requests via natural language              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Phase 3: Completion                            │
│                                                             │
│  All tasks complete:                                        │
│  - Status panel shows completion                           │
│  - Report duration and scope changes                       │
│  - Ready for next goal                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Concept Hierarchy

```
Project Level
└── POR.md                              # Strategic board (optional)
    └── Tasks                           # Work items (planned upfront)
        ├── T001-oauth/task.yaml        # status: complete
        ├── T002-logging/task.yaml      # status: complete
        ├── T003-dashboard/task.yaml    # status: active ← current
        ├── T004-profile/task.yaml      # status: planned
        └── T005-notifications/task.yaml # status: planned
            └── Steps                   # Within each task
                ├── S1: Design
                ├── S2: Implement
                └── S3: Test
```

---

## Command Design

### Single Command: `/task`

Following the design principle, only one task-related command:

| Usage | Description | Implementation |
|-------|-------------|----------------|
| `/task` | Show all tasks with progress | Script reads task.yaml files |
| `/task T003` | Show specific task details | Script reads one task.yaml |
| `/task done` | Show completed tasks only | Filter by status=complete |
| `/task active` | Show active tasks only | Filter by status=active |
| `/task blocked` | Show blocked tasks/steps | Filter by any blocked step |
| `/task --limit N` | Show first N tasks | For large projects (100+) |

**Cross-platform:**
- TUI: `/task`
- Telegram: `/task`
- Slack/Discord: `!task`

**Scalability:** For projects with 100+ tasks, default `/task` shows:
- Summary line (X/Y complete)
- Active tasks (full detail)
- Next 3 planned tasks (brief)
- "...and N more planned" if truncated

### NOT Commands (Use Natural Language)

| Operation | How User Does It | Why |
|-----------|------------------|-----|
| Replan | "Let's replan, the approach is wrong" | Needs Agent analysis |
| Modify task | "Change T003's goal to..." | Needs Agent judgment |
| Ask about progress | "Why is T003 taking so long?" | Needs Agent context |
| Skip a task | "Skip T004, we don't need it" | Needs Agent to understand impact |

---

## `/task` Command Output

### `/task` - All Tasks

**TUI Output:**

```
┌─ Project Progress ──────────────────────────────────────────┐
│                                                             │
│  📊 Progress: 2/5 (40%)                                     │
│  ████████████████░░░░░░░░░░░░░░░░░░░░░░                     │
│                                                             │
│  Scope: 5 tasks (no changes) │ Quick: 3 done                │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│  ✓ T001  OAuth Setup         4/4                           │
│  ✓ T002  Login Page          3/3                           │
│  → T003  Dashboard           1/4   S2: Implement backend    │
│  ○ T004  User Profile        0/3                           │
│  ○ T005  Notifications       0/2                           │
│                                                             │
│  Legend: ✓ complete │ → active │ ○ planned                  │
└─────────────────────────────────────────────────────────────┘
```

**IM Output:**

```
📊 Progress: 2/5 (40%)
████████░░░░░░░░░░░░

✓ T001 OAuth Setup    4/4
✓ T002 Login Page     3/3
→ T003 Dashboard      1/4 ← S2
○ T004 User Profile   0/3
○ T005 Notifications  0/2

Scope: 5 │ Quick: 3 done
```

### `/task T003` - Specific Task

**TUI Output:**

```
┌─ T003: Dashboard ────────────────────────────────────────────┐
│                                                              │
│  Goal: Users can view their activity history and stats       │
│  Status: active │ Progress: 1/4 (25%)                        │
│                                                              │
│  Steps:                                                      │
│  ─────────────────────────────────────────────────────────  │
│  ✓ S1  Design API endpoints                                 │
│        Done: API spec documented in docs/api/dashboard.md    │
│                                                              │
│  → S2  Implement backend endpoints              ← current    │
│        Done: All endpoints return correct data, tests pass   │
│                                                              │
│  ○ S3  Build frontend components                             │
│        Done: Dashboard page renders with real data           │
│                                                              │
│  ○ S4  Integration testing                                   │
│        Done: E2E tests pass for all flows                    │
│                                                              │
│  Started: Jan 16, 15:00 │ Elapsed: 2h 15m                   │
└──────────────────────────────────────────────────────────────┘
```

**IM Output:**

```
📋 T003: Dashboard
Status: active │ 1/4 (25%)

✓ S1 Design API endpoints
→ S2 Implement backend ← now
○ S3 Build frontend
○ S4 Integration testing

Started: Jan 16, 15:00
```

### `/task done` - Completed Tasks

```
┌─ Completed Tasks ────────────────────────────────────────────┐
│                                                              │
│  ✓ T002  Login Page       3/3    Jan 16, 14:30   1h 45m     │
│  ✓ T001  OAuth Setup      4/4    Jan 15, 16:00   2h 30m     │
│                                                              │
│  Total: 2 completed                                          │
└──────────────────────────────────────────────────────────────┘
```

---

## Task Panel (TUI Feature)

The Task Panel is a **distinctive CCCC feature** — a dedicated, expandable UI component for task visibility.

### Design Principles

1. **Collapsed by default** — One line, no more space than current status
2. **Expand on demand** — Full task list when needed
3. **Zero-friction toggle** — Keyboard (`T`) or mouse click
4. **Information-dense when expanded** — Worth the screen space

### Collapsed State (Default)

```
┌─ Status ─────────────────────────────────────────────────────┐
│ PeerA: working │ PeerB: idle │ 📊 2/5 → T003 [S2]       [T] │
└─────────────────────────────────────────────────────────────┘
```

- Single line, minimal footprint
- `[T]` indicates expandable (press T or click)
- Shows: overall progress, current task, current step

### Expanded State (Press T or Click)

```
┌─ Status ─────────────────────────────────────────────────────┐
│ PeerA: working │ PeerB: idle │ Foreman: off                  │
├─ Blueprint (2/5 · 40%) ──────────────────────────────── [T] ─┤
│                                                              │
│  ✓ T001 OAuth Setup      4/4    ○ T004 User Profile     0/3  │
│  ✓ T002 Login Page       3/3    ○ T005 Notifications    0/2  │
│  → T003 Dashboard        1/4                                 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Layout features:**

| Element | Purpose |
|---------|---------|
| Two-column layout | Save vertical space, see all tasks at once |
| Task name + progress | Core info at a glance |
| Status icons | ✓ complete, → active, ○ planned |
| `[T]` remains visible | Reminder to collapse |

**简化说明：**
- 不显示当前步骤名称 (只显示 1/4，不显示 S2: xxx)
- 不显示 Quick tasks 计数 (低 ROI)
- 不显示 Scope 变化 (信息在 ledger 中)

### Large Project State (10+ Tasks)

```
├─ Blueprint (5/12 · 42%) ─────────────────────────────── [T] ─┤
│                                                              │
│  ✓ T001-T005 (5 complete)                                    │
│  → T006 Payment Integration  2/4                             │
│  ○ T007 Email Service        0/3   ○ T010 Analytics    0/4   │
│  ○ T008 Admin Panel          0/5   ○ T011 Export       0/2   │
│  ○ T009 API Rate Limiting    0/3   ○ T012 Backup       0/3   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- Completed tasks collapsed to summary line
- Active task shown with progress
- Planned tasks in two-column layout

### Interaction Design

| Action | Effect |
|--------|--------|
| Press `T` | Toggle expand/collapse |
| Click `[T]` | Toggle expand/collapse |
| Click task row | Show task detail (equivalent to `/task T003`) |
| Press `Esc` (when expanded) | Collapse |

### State Variations

**Collapsed state variations:**

| Situation | Display |
|-----------|---------|
| Planning phase | `│ ... │ 📋 Planning...                      [T] │` |
| No tasks | `│ ... │ No tasks                           [T] │` |
| Execution | `│ ... │ 📊 2/5 → T003 [S2]                 [T] │` |
| Task blocked | `│ ... │ 📊 2/5 ⚠ T003 BLOCKED              [T] │` |
| All complete | `│ ... │ ✓ 5/5 Complete                    [T] │` |

**Expanded state variations:**

| Situation | Display |
|-----------|---------|
| Planning phase | Shows "Planning in progress..." with spinner |
| Blocked task | Blocked task highlighted with `⚠` |
| All complete | Shows `✓ All X tasks complete` |

### TUI Layout Integration

```
┌─────────────────────────────────────────────────────────────┐
│  ┌─ Task Panel ─────────────────────────────────────────┐  │
│  │ Collapsed: 1 line │ Expanded: 6-10 lines             │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌─ Timeline ───────────────────────────────────────────┐  │
│  │ Message flow + task events (scrollable)              │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌─ Input ──────────────────────────────────────────────┐  │
│  │ > Command input                                      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Notes

**prompt_toolkit components:**

```python
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit

class TaskPanel:
    def __init__(self):
        self.expanded = False

    def toggle(self):
        self.expanded = not self.expanded

    def get_container(self):
        return ConditionalContainer(
            content=self._expanded_content() if self.expanded else self._collapsed_content(),
            filter=Condition(lambda: has_tasks())
        )
```

**Key bindings:**

```python
@bindings.add('t', filter=~is_searching)
def toggle_task_panel(event):
    task_panel.toggle()
    event.app.invalidate()
```

---

## Status Panel

The Status Panel is now **integrated into Task Panel** as its header row.

### Status Line (Always Visible)

```
│ PeerA: working │ PeerB: idle │ Foreman: off                  │
```

### Combined with Task Progress

**Collapsed:**
```
│ PeerA: working │ PeerB: idle │ 📊 2/5 → T003 [S2]       [T] │
```

**Expanded:**
```
│ PeerA: working │ PeerB: idle │ Foreman: off                  │
├─ Blueprint (2/5 · 40%) ──────────────────────────────── [T] ─┤
```

### Status Indicators

| Indicator | Meaning |
|-----------|---------|
| `📊 X/Y` | Task progress (collapsed) |
| `📋 Planning...` | Planning phase in progress |
| `⚠ BLOCKED` | Current task is blocked |
| `✓ Complete` | All tasks done |
| `[T]` | Press T to toggle Task Panel |

---

## File Structure

```
docs/por/
├── POR.md                          # Strategic board (optional)
├── scope.yaml                      # Scope tracking (auto-managed)
├── T001-oauth/
│   └── task.yaml
├── T002-logging/
│   └── task.yaml
├── T003-dashboard/
│   └── task.yaml
└── ...
```

---

## task.yaml Schema

### Design Principle: Minimal Schema, Orchestrator-Managed

```
┌─────────────────────────────────────────────────────────────┐
│  Agent 负担最小化：只需在消息中包含 progress marker         │
│  Orchestrator 负责：解析 marker，更新 task.yaml            │
│  可计算字段：不存储，实时计算 (current, progress)           │
└─────────────────────────────────────────────────────────────┘
```

### Example

```yaml
# docs/por/T003-dashboard/task.yaml

id: T003
name: User Dashboard
goal: Users can view their activity history and account stats
status: active    # planned | active | complete

steps:
  - id: S1
    name: Design API endpoints
    done: API spec documented in docs/api/dashboard.md
    status: complete

  - id: S2
    name: Implement backend endpoints
    done: All endpoints return correct data, tests pass
    status: in_progress

  - id: S3
    name: Build frontend components
    done: Dashboard page renders with real data
    status: pending

  - id: S4
    name: Integration testing
    done: E2E tests pass for all dashboard flows
    status: pending
```

**注意：没有 `current`、`progress`、`started`、`completed` 字段 — 这些实时计算。**

### Fields

| Field | Type | Description | Set By |
|-------|------|-------------|--------|
| `id` | string | Task ID (T001, T002...) | Agent (规划时) |
| `name` | string | Task name | Agent (规划时) |
| `goal` | string | Success criteria | Agent (规划时) |
| `status` | enum | planned \| active \| complete | Orchestrator |
| `steps` | array | 2-8 steps | Agent (规划时) |

### Step Fields

| Field | Type | Description | Set By |
|-------|------|-------------|--------|
| `id` | string | Step ID (S1, S2...) | Agent (规划时) |
| `name` | string | Step description | Agent (规划时) |
| `done` | string | Completion criteria | Agent (规划时) |
| `status` | enum | pending \| in_progress \| complete | Orchestrator |

### Computed at Runtime (Not Stored)

| Field | Computation |
|-------|-------------|
| `current` | First step where status != complete |
| `progress` | Count(status=complete) / len(steps) |
| `started` | From ledger (task_activated event) |
| `completed` | From ledger (task_completed event) |

---

## scope.yaml Schema (Simplified)

```yaml
# docs/por/scope.yaml (orchestrator-managed)

original: 5              # Initial planned count (set once)
current: 5               # Current count (updated on scope change)
```

### scope.yaml 管理规则

| 事件 | 谁负责 | 操作 |
|------|--------|------|
| 初始规划完成 | Orchestrator | 创建 scope.yaml，设置 original = current = 任务数 |
| Agent 添加新任务 | Orchestrator | 检测新 task.yaml，current += 1 |
| Agent 删除任务 | Orchestrator | 检测删除，current -= 1 |
| Quick task promoted | Orchestrator | 检测 promoted marker，current += 1 |

**Agent 永远不直接写 scope.yaml** — Orchestrator 通过检测 docs/por/ 目录变化自动维护。

**Scope change 记录在 ledger：**
```json
{"type": "scope_change", "from": 5, "to": 6, "added": ["T006"], "reason": "...", "by": "PeerA"}
```

### 任务编号规则

Agent 创建新任务时:

1. 扫描 `docs/por/` 目录，找出已有的 T### 编号
2. 取最大编号 + 1 作为新任务编号
3. 如果目录为空，从 T001 开始

```python
# Agent 伪代码
existing_ids = [int(d[1:4]) for d in listdir("docs/por/") if d.startswith("T")]
next_id = max(existing_ids, default=0) + 1
new_task_id = f"T{next_id:03d}"  # T001, T002, ...
```

**跨目标延续**: 如果已有 T001-T005，新目标从 T006 开始。

---

## Progress Marker (Core Mechanism)

### Why Progress Markers?

```
问题：要求 Agent 直接更新 task.yaml → Agent 可能忘记 → 数据过时
解决：Agent 在消息中包含 progress marker → Orchestrator 解析并更新
```

**Agent 负担：** 一行文本
**Orchestrator 负担：** 解析 + 文件更新
**可靠性：** 高 (marker 是消息的自然部分)

### Marker Format

```
progress: <task_id>.<step_id> <action>
```

### Actions

| Action | Meaning | Orchestrator Response |
|--------|---------|----------------------|
| `start` | 开始任务 | task.status → active, S1.status → in_progress |
| `done` | 步骤完成 | step.status → complete, next step → in_progress |
| `blocked` | 步骤阻塞 | 记录到 ledger，发送通知 (step.status 不变) |
| `promoted` | Quick task 升级 | 更新 scope.yaml，记录到 ledger |

### Blocked 行为说明

`blocked` 是**通知性质**，不改变 step.status:
- 发送 `blocked` 后，step 仍然是 `in_progress`
- 阻塞解除后，直接发 `done` 完成步骤
- 无需专门的 "unblock" marker

```
progress: T001.S2 blocked: waiting for API key   ← 通知阻塞
... 阻塞解除后 ...
progress: T001.S2 done                           ← 直接完成
```

### 多 Marker 支持

一条消息可包含多个 markers (按顺序处理):

```
<TO_USER>
S1 和 S2 都完成了，开始 S3。

progress: T001.S1 done
progress: T001.S2 done
</TO_USER>
```

Orchestrator 按出现顺序依次处理。

### Examples

**开始任务：**
```
<TO_USER>
开始实现用户认证模块。

首先设计 API 结构。

progress: T001 start
</TO_USER>
```

**完成步骤：**
```
<TO_USER>
API 设计完成，文档已更新到 docs/api/auth.md。

开始实现后端接口。

progress: T001.S1 done
</TO_USER>
```

**完成最后一步 (自动完成任务)：**
```
<TO_USER>
所有测试通过，认证模块完成。

progress: T001.S4 done
</TO_USER>
```
→ Orchestrator 检测到所有 step complete → task.status → complete

**阻塞：**
```
<TO_USER>
需要 API key 才能继续，等待用户提供。

progress: T001.S2 blocked: waiting for API key
</TO_USER>
```

### Orchestrator Processing

```python
def process_message(message: str, peer: str):
    # 提取 progress marker
    match = re.search(r'progress:\s*(\S+)\s+(\w+)(?::\s*(.+))?', message)
    if not match:
        return

    target, action, reason = match.groups()

    if '.' in target:
        task_id, step_id = target.split('.')
    else:
        task_id, step_id = target, None

    if action == 'start':
        activate_task(task_id)
    elif action == 'done':
        complete_step(task_id, step_id)
    elif action == 'blocked':
        log_blocked(task_id, step_id, reason)

    # 写入 ledger
    log_event({
        'type': f'step_{action}' if step_id else f'task_{action}',
        'task': task_id,
        'step': step_id,
        'by': peer,
        'reason': reason
    })
```

### Fallback: Manual Detection

如果 Agent 忘记包含 marker，Orchestrator 可尝试从消息内容推断：
- 检测 "完成"、"done"、"finished" 等关键词
- 检测 commit 消息引用
- 检测测试结果

但这是 **备用机制**，不应依赖。

---

## Task Lifecycle

```
planned → active → complete → archived
```

| Transition | Trigger |
|------------|---------|
| → planned | Agent creates task during planning |
| planned → active | Agent starts working |
| active → complete | All steps complete |
| complete → archived | User/auto archive |

---

## Planning Threshold

### Concrete Threshold Rules

Agent uses these heuristics to decide if formal planning is needed:

**Skip planning (Quick Task) if ALL of these are true:**

| Criterion | Threshold |
|-----------|-----------|
| Files affected | ≤ 2 files |
| Lines changed | ≤ 50 lines total |
| Dependencies | No new dependencies |
| Type | bugfix, typo, config, docs, minor refactor |
| Scope | Single concern (not cross-cutting) |

**Require planning if ANY of these are true:**

| Criterion | Threshold |
|-----------|-----------|
| Files affected | ≥ 3 files |
| New components | Any new module/class/API |
| User-facing change | New feature, UI change |
| Architecture impact | Changes data flow, adds service |
| Testing needed | Requires new test suite |
| Uncertainty | Agent unsure about approach |

### Quick Task Execution

**When skipped:**
- Agent executes directly
- No task.yaml created
- Ledger records: `{"type": "quick_task", "description": "...", "by": "PeerA"}`

### Quick Task Promotion (Recovery Mechanism)

**Problem:** Agent misjudges, starts as quick task, discovers complexity mid-execution.

**Solution:** Agent creates task.yaml mid-execution and sends promotion marker.

```
Quick task started: "Fix login error"
     ↓
Agent discovers: Multiple auth flows affected
     ↓
Agent creates task.yaml (with work done marked as complete)
     ↓
Agent sends: progress: T001 promoted
     ↓
Orchestrator updates scope.yaml, continues tracking
```

**Step 1: Agent creates task.yaml**

```yaml
id: T001
name: Fix Login Authentication
goal: All login flows work correctly with proper error handling
status: active

steps:
  - id: S1
    name: Audit all auth flows
    done: All affected code paths identified
    status: complete              # ← Work already done

  - id: S2
    name: Implement fixes
    done: All flows return correct responses
    status: in_progress           # ← Current work

  - id: S3
    name: Add tests
    done: All auth flows have test coverage
    status: pending
```

**Step 2: Agent sends marker in TO_USER**

```
<TO_USER>
发现登录问题比预期复杂，涉及多个认证流程。已创建正式任务追踪。

目前已完成流程审计，正在实现修复。

progress: T001 promoted
</TO_USER>
```

**Orchestrator response:**
- Detects `promoted` marker
- Creates/updates scope.yaml
- Logs to ledger: `{"type": "task_promoted", "task_id": "T001", "reason": "from quick task"}`

**Key principle:** Work already done is credited. Agent marks completed work as `status: complete` when creating task.yaml.

---

## Planning Phase

### Dual-Peer Mode

```
User goal → PeerA creates plan → PeerB reviews
                                     ↓
                         ├── Approves → Execute
                         ├── Counters → Revise (max 2 rounds)
                         └── Timeout (10 min) → Execute with warning
```

### Single-Peer Mode

```
User goal → Peer creates plan → Execute immediately
```

### Agent Planning Behavior

1. Analyze goal complexity
2. If ambiguous, explore codebase first
3. Break into 3-10 tasks
4. Each task: 2-8 steps, clear done criteria
5. Create all task.yaml files (status: planned)
6. Create scope.yaml
7. Post summary, start execution

---

## Replan (Natural Language)

User triggers replan via natural language:

```
User: "The plan is wrong, let's rethink this"
User: "We need a different approach for T003-T005"
User: "Skip T004, add a new task for caching instead"
```

### Replan File Handling

| 任务状态 | 处理方式 |
|----------|----------|
| complete | 保留不动 |
| active | 可修改 task.yaml 或删除 |
| planned | 可修改、删除或新建 |

**Agent 操作流程:**

```
1. 识别 replan 意图
     ↓
2. 分析需要改变什么
     ↓
3. 文件操作:
   - 保留已完成的 task.yaml
   - 修改/删除活跃或计划中的 task.yaml
   - 创建新的 task.yaml (如需要)
     ↓
4. Orchestrator 自动更新 scope.yaml (检测文件变化)
     ↓
5. Agent 发送 replan 总结给 TO_USER
     ↓
6. 继续执行
```

**Replan 是规划操作** — Agent 直接修改 task.yaml 文件 (与初始规划相同)，不需要特殊 marker。

**Not a command** because:
- Needs Agent to understand context
- Needs Agent to make decisions
- Different replan requests need different handling

---

## Blocked Step Handling

当 Agent 遇到阻塞:

1. 发送 `progress: T003.S2 blocked: reason` marker
2. Orchestrator 记录到 ledger，发送通知
3. Status Panel 显示: `⚠ T003 BLOCKED`
4. step.status 保持 `in_progress` (blocked 是通知性质)

**解除阻塞:**

```
User: "The database issue is fixed, continue T003"
     ↓
Agent 继续工作，完成后发送:
progress: T003.S2 done
```

无需 "unblock" marker — 直接用 `done` 完成步骤。

---

## Multi-Agent Coordination

| Action | Dual-Peer | Single-Peer |
|--------|-----------|-------------|
| Planning | PeerA (+ PeerB review) | Peer |
| Execution | Either (by handoff) | Peer |
| Progress updates | Whoever is working | Peer |
| Scope changes | Either (notify other) | Peer |

**Conflict avoidance:**
- One task active at a time (default)
- One step in_progress per task
- Atomic file writes

---

## Timeline Events

```
[10:00] 📋 Goal: "Add user authentication"
[10:00] ⚡ Below threshold: No (proceeding to planning)
[10:05] 📋 Plan created: 5 tasks
[10:05] → T001 activated
[10:45] ✓ T001 S1 complete
[11:30] ✓ T001 complete (4/4)
[11:30] → T002 activated
...
[14:00] ⚡ Quick task: "Fix typo in README"
...
[16:00] ⚡ Scope: +1 task (T006-mfa)
...
[18:30] ✓ All complete (6/6)
```

---

## IM Notifications

**Plan created:**
```
📋 Plan: 5 tasks
T001 OAuth Setup (4 steps)
T002 Login Page (3 steps)
T003 Dashboard (4 steps)
T004 User Profile (3 steps)
T005 Notifications (2 steps)
Starting T001...
```

**Task complete:**
```
✓ T001 complete
Progress: 1/5 (20%)
→ Starting T002
```

**All complete:**
```
🎉 All complete!
5/5 tasks │ 8h 30m
Quick: 3 │ Scope: no changes
```

---

## System Prompt Specification

**This section defines the exact instructions injected into Agent system prompts.**

### Core Task Protocol Block

```markdown
## Task Management Protocol

You follow a structured task management protocol for complex work.

### When You Receive a Message

1. **Detect if this is a new goal** requiring planning:
   - Goal signals: action verbs (add, implement, create, build), outcome descriptions, feature scope
   - Not a goal: questions, follow-ups to existing work, single-file fixes

2. **If it's a goal, check threshold** — skip formal planning if ALL true:
   - ≤ 2 files affected
   - ≤ 50 lines changed
   - No new dependencies
   - Type: bugfix, typo, config, docs, minor refactor
   - Single concern (not cross-cutting)

3. **Quick task** (below threshold):
   - Execute directly
   - Report completion in TO_USER
   - If you discover it's more complex, create task.yaml and continue with tracking

4. **Formal planning** (above threshold):
   - Create task breakdown (3-10 tasks, each with 2-8 steps)
   - Write task.yaml files to `docs/por/T###-slug/task.yaml`
   - Post plan summary in TO_PEER (dual-peer) or TO_USER (single-peer)
   - Then begin execution

### Task File Format

Create `docs/por/T###-slug/task.yaml`:

```yaml
id: T001
name: Short descriptive name
goal: Clear success criteria (what "done" looks like)
status: planned    # Orchestrator will update this

steps:
  - id: S1
    name: First step description
    done: Concrete completion criteria
    status: pending

  - id: S2
    name: Second step description
    done: Concrete completion criteria
    status: pending
```

### Progress Markers (IMPORTANT)

**You don't need to update task.yaml during execution.** Instead, include a progress marker in your TO_USER messages:

```
progress: <task_id>.<step_id> <action>
```

**Actions:**
- `start` — Starting a task (e.g., `progress: T001 start`)
- `done` — Completed a step (e.g., `progress: T001.S1 done`)
- `blocked` — Step is blocked (e.g., `progress: T001.S2 blocked: reason`)

**Example message:**
```
<TO_USER>
API 设计完成，文档已更新。开始实现后端接口。

progress: T001.S1 done
</TO_USER>
```

The orchestrator will parse this marker and update task.yaml automatically.

### Replan Recognition

If user says things like:
- "replan", "rethink", "change the plan"
- "wrong approach", "different way"
- "skip task", "add task", "remove task"

Then revise the task breakdown, preserve completed work.

### Blocked Steps

If you can't complete a step:
1. Include `progress: T###.S# blocked: reason` in your message
2. Explain the blocker in TO_USER
3. Continue with other work if possible
```

### Dual-Peer Coordination Block

```markdown
## Dual-Peer Task Coordination

### Planning Phase
- **PeerA**: Creates initial task breakdown, posts TO_PEER for review
- **PeerB**: Reviews plan, may COUNTER with improvements (max 2 rounds, 10 min timeout)
- After consensus or timeout, execution begins

### Execution Phase
- Either peer can work on active task
- Whoever completes a step updates task.yaml and posts progress
- Handoff continues normally — task context is in files, not conversation

### Scope Changes
- Either peer can add tasks (update scope.yaml with reason)
- Notify the other peer via TO_PEER
```

### Planning Output Format

When Agent creates a plan, post this summary:

**TO_PEER (dual-peer mode):**
```
📋 PLAN PROPOSAL

Goal: [user's goal]

Tasks:
T001 [name] - [brief description] (N steps)
T002 [name] - [brief description] (N steps)
...

Total: X tasks

Please review. COUNTER if you see improvements, or let's proceed.
```

**TO_USER (single-peer or after consensus):**
```
📋 Plan created: X tasks

T001 [name] (N steps)
T002 [name] (N steps)
...

Starting T001...
```

---

## Agent Behavior Specifications

### Goal Received

```python
def handle_goal(goal):
    # Goal detection (see System Prompt)
    if not is_goal(goal):
        respond_or_execute_simple(goal)
        return

    # Threshold check
    if is_quick_task(goal):
        execute_directly(goal)
        log_quick_task()
        # If complexity discovered later, promote_to_task()
        return

    # Planning (includes discovery if needed)
    if is_ambiguous(goal):
        explore_and_refine(goal)

    tasks = create_task_breakdown(goal)
    create_task_files(tasks)
    create_scope_file(len(tasks))

    if dual_peer_mode:
        post_plan_for_review()
        # Wait for consensus or timeout

    # Execution
    execute_tasks()
```

### Step Completion

```python
def complete_step(task_id, step_id):
    task = read_task(task_id)
    step = get_step(task, step_id)

    step.status = "complete"

    next_step = get_next_step(task)
    if next_step:
        next_step.status = "in_progress"
        task.current = next_step.id
    else:
        task.status = "complete"
        task.completed = now()
        activate_next_task()

    task.update_progress()
    save_task(task)
    post_progress_update()
```

### Replan Recognition

Agent recognizes replan intent from:
- "replan", "rethink", "change the plan"
- "wrong approach", "different way"
- "skip task", "add task", "remove task"
- "the plan doesn't work"

---

## Progress Reporting Guidelines

### When to Include Progress Markers

| Event | Marker | Example |
|-------|--------|---------|
| Start task | `progress: T### start` | `progress: T001 start` |
| Complete step | `progress: T###.S# done` | `progress: T001.S1 done` |
| Blocked | `progress: T###.S# blocked: reason` | `progress: T001.S2 blocked: waiting for API key` |

**Rule: Include marker when state changes, Orchestrator handles the rest.**

### Message Format

Include progress marker naturally at the end of TO_USER messages:

```
<TO_USER>
API endpoint 实现完成，测试通过。开始前端组件开发。

progress: T003.S2 done
</TO_USER>
```

### When to Report (TO_USER vs TO_PEER)

| Event | TO_USER | TO_PEER |
|-------|---------|---------|
| Planning complete | ✓ Plan summary | ✓ Plan for review |
| Step complete | ✓ With marker | — |
| Task complete | ✓ With marker | Brief handoff |
| Blocked | ✓ With marker + details | ✓ If need help |

**Don't** create separate progress-only messages. Include marker in regular work updates.

---

## Configuration

```yaml
# .cccc/settings/policies.yaml

task_planning:
  # Planning timeout (dual-peer)
  planning:
    review_timeout_minutes: 10
    max_counter_rounds: 2
    on_timeout: proceed_with_warning

  # Display options
  display:
    show_quick_tasks: true
```

---

## Pydantic Schema (Simplified)

```python
from pydantic import BaseModel, Field
from typing import Literal

class Step(BaseModel):
    """Step within a task. Status managed by Orchestrator."""
    id: str = Field(..., pattern=r"^S\d+$")
    name: str
    done: str  # Completion criteria
    status: Literal["pending", "in_progress", "complete"] = "pending"

class TaskDefinition(BaseModel):
    """Task definition. Status managed by Orchestrator via progress markers."""
    id: str = Field(..., pattern=r"^T\d{3}$")
    name: str
    goal: str  # Success criteria
    status: Literal["planned", "active", "complete"] = "planned"
    steps: list[Step] = Field(..., min_length=2, max_length=8)

    # Computed properties (not stored in YAML)
    @property
    def current_step(self) -> str | None:
        """First non-complete step."""
        for step in self.steps:
            if step.status != "complete":
                return step.id
        return None

    @property
    def progress(self) -> str:
        """Progress as 'X/Y' string."""
        complete = sum(1 for s in self.steps if s.status == "complete")
        return f"{complete}/{len(self.steps)}"

    @property
    def progress_percent(self) -> int:
        """Progress as percentage."""
        complete = sum(1 for s in self.steps if s.status == "complete")
        return int(complete / len(self.steps) * 100)

    @property
    def is_complete(self) -> bool:
        """All steps complete."""
        return all(s.status == "complete" for s in self.steps)

class ProjectScope(BaseModel):
    """Minimal scope tracking. History in ledger."""
    original: int  # Set once at planning
    current: int   # Updated on scope change
```

### Task Manager Methods

```python
class TaskManager:
    """Orchestrator component for task management."""

    def activate_task(self, task_id: str):
        """Activate a task (planned → active)."""
        task = self.load_task(task_id)
        task.status = "active"
        task.steps[0].status = "in_progress"
        self.save_task(task)
        self.log_event("task_activated", task_id)

    def complete_step(self, task_id: str, step_id: str):
        """Complete a step, advance to next or complete task."""
        task = self.load_task(task_id)

        # Mark step complete
        for i, step in enumerate(task.steps):
            if step.id == step_id:
                step.status = "complete"
                # Advance to next step if exists
                if i + 1 < len(task.steps):
                    task.steps[i + 1].status = "in_progress"
                break

        # Check if task complete
        if task.is_complete:
            task.status = "complete"
            self.log_event("task_completed", task_id)
            self.activate_next_task()
        else:
            self.log_event("step_completed", task_id, step_id)

        self.save_task(task)
```

---

## Implementation Plan

### Phase 1: Core (~4 days)

| Task | File | Effort |
|------|------|--------|
| Pydantic schemas | `orchestrator/task_schema.py` | 0.5d |
| Task manager | `orchestrator/task_manager.py` | 1d |
| **Progress marker parser** | `orchestrator/handoff.py` | 0.5d |
| `/task` command | `tui_ptk/app.py` | 0.5d |
| System prompt injection | `prompt_weaver.py` | 1d |
| Unit tests | `tests/test_task_*.py` | 0.5d |

### Phase 2: Task Panel UI (~2 days)

| Task | File | Effort |
|------|------|--------|
| Task Panel component | `tui_ptk/task_panel.py` | 1d |
| Expand/collapse + T key | `tui_ptk/app.py` | 0.5d |
| Large project folding | `tui_ptk/task_panel.py` | 0.5d |

### Phase 3: Agent Integration (~2.5 days)

| Task | File | Effort |
|------|------|--------|
| Planning behavior prompts | `prompt_weaver.py` | 1d |
| Planning timeout | `orchestrator/handoff.py` | 0.5d |
| Replan recognition | `orchestrator/handoff.py` | 0.5d |
| Ledger events | `orchestrator/events.py` | 0.5d |

### Phase 4: IM & Polish (~2 days)

| Task | File | Effort |
|------|------|--------|
| IM `/task` command | `adapters/bridge_*.py` | 0.5d |
| IM notifications | `adapters/bridge_*.py` | 0.5d |
| Timeline events | `tui_ptk/app.py` | 0.5d |
| Edge cases & testing | Various | 0.5d |

### Total: ~10.5 days

---

## File Modification Summary

### New Files

| File | Purpose |
|------|---------|
| `orchestrator/task_schema.py` | Pydantic models |
| `orchestrator/task_manager.py` | Task CRUD operations |
| `tui_ptk/task_panel.py` | Task Panel UI component |

### Modified Files

| File | Changes |
|------|---------|
| `prompt_weaver.py` | Task context injection, System Prompt |
| `orchestrator/handoff.py` | Phase detection, message parsing |
| `orchestrator/status.py` | Progress in status panel |
| `orchestrator/events.py` | Task event types |
| `tui_ptk/app.py` | `/task` command, T key binding |
| `adapters/bridge_telegram.py` | `/task` command |
| `adapters/bridge_slack.py` | `!task` command |
| `adapters/bridge_discord.py` | `!task` command |
| `settings/policies.yaml` | Planning config |

---

## Summary

### What This Design Solves

| Problem | Solution |
|---------|----------|
| Unknown total tasks | Plan before execute |
| Trivial tasks overhead | Threshold check (concrete rules) |
| Agent doesn't know what's a goal | Goal Detection heuristics |
| Agent may forget to update files | **Progress Markers + Orchestrator-managed** |
| Ambiguous goals | Agent explores first |
| Planning deadlock | Timeout mechanism |
| Wrong plan | Replan via natural language |
| Agent doesn't know protocol | System Prompt Specification |
| 100+ tasks UI clutter | Smart filtering + limit |
| TUI lacks task visibility | Task Panel (expand/collapse) |

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Commands | `/task` only | Script for data, Agent for decisions |
| **Progress tracking** | **Agent sends marker, Orchestrator updates** | Reliable, minimal Agent burden |
| Goal detection | Agent heuristics | Avoids ceremony, fallback is asking |
| Schema | **Minimal (6 fields)** | Computed fields not stored |
| Task Panel | Expand/collapse | Best of both: minimal default, detail on demand |
| Replan | Natural language | Needs Agent intelligence |

### Core Data Flow

```
Agent 完成步骤 → 消息中包含 "progress: T001.S1 done"
                        ↓
              Orchestrator 解析 marker
                        ↓
              更新 task.yaml (step.status)
                        ↓
              Task Panel 实时显示
```

### User Mental Model

```
Quick fix?      → Agent just does it (can create task.yaml if complex)
Complex goal?   → Agent plans first, then executes
See progress?   → Press T (Task Panel) or /task command
Full task list? → Press T to expand Task Panel
Too many tasks? → /task active or /task --limit 10
Change plan?    → Tell Agent in natural language
```

### Key Metrics

| Metric | Value |
|--------|-------|
| Commands | 1 (`/task` with variants) |
| task.yaml fields | 6 (id, name, goal, status, steps, step.status) |
| New files | 3 |
| Modified files | ~8 |
| Implementation | ~10.5 days |

### Version 7.0 Key Features

| Feature | Description |
|---------|-------------|
| **Progress Markers** | Agent 一行标记，Orchestrator 自动更新 |
| **Orchestrator-managed** | 状态更新可靠，Agent 无需维护文件 |
| Goal Detection | Agent 判断何时开始规划 |
| Concrete Threshold | ≤2 files, ≤50 lines → quick task |
| Task Panel | TUI 特色功能，T 键展开/收缩 |
| Simplified Schema | 无冗余字段，computed fields 不存储 |

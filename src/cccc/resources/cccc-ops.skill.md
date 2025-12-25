# CCCC-OPS Skill

> This is the operational playbook for the CCCC multi-agent collaboration system. All actors share this skill but execute different sections based on their role.

## 0) First, Confirm Your Role

Before executing any operation, confirm your role:
- Check the `Identity` line in the SYSTEM message injected at startup
- Or call `cccc_group_info` to get group information

Your role determines your permission boundaries:
- **foreman**: Can manage group structure and other actors
- **peer**: Can only manage yourself, focus on executing tasks

## 1) Foreman Playbook

If you are a foreman:

### Responsibilities
- Plan and decompose tasks
- Assign tasks to peers
- Approve critical decisions
- Monitor overall progress

### Workflow
1. After receiving a task, call `cccc_context_get` to get current context
2. Update vision/sketch (if needed)
3. Create milestones and tasks
4. Assign tasks to peers (via `cccc_message_send`)
5. Monitor inbox, approve peer requests
6. Update milestone status when complete

### Message Format
- Assign task: `@peer-id Please implement xxx`
- Approve: `@peer-id Approved/Rejected xxx`
- Status query: `@all Please report progress`

## 2) Peer Playbook

If you are a peer:

### Responsibilities
- Execute assigned tasks
- Report progress and issues
- Request approval (if needed)

### Workflow
1. Check inbox (`cccc_inbox_list`)
2. Get context (`cccc_context_get`)
3. Update status (`cccc_presence_update`)
4. Execute task, update step progress (`cccc_task_update`)
5. Report completion (`cccc_message_send` to foreman)
6. Mark messages as read (`cccc_inbox_mark_read`)

### Message Format
- Report completion: `@foreman Task xxx completed`
- Request approval: `@foreman Please approve xxx`
- Report issue: `@foreman Encountered issue: xxx`

## 3) Common Rules

### Message Delivery
- Use `to` parameter to specify recipients
- `@all` = everyone
- `@foreman` = foreman
- `@peers` = all peers
- specific actor id = specific actor

### Context Sync
- Call `cccc_context_get` at the start of each session
- Record important findings to notes (`cccc_note_add`)
- Record useful files/URLs to references (`cccc_reference_add`)

### Task Management
- Tasks have 3-7 steps
- Update status after completing each step
- Update task status to done after completing the entire task

## 4) MCP Tools Quick Reference

### Messages
- `cccc_inbox_list`: Get unread messages
- `cccc_inbox_mark_read`: Mark as read
- `cccc_message_send`: Send message
- `cccc_message_reply`: Reply to message

### Context
- `cccc_context_get`: Get full context
- `cccc_context_sync`: Batch sync operations
- `cccc_task_update`: Update task progress
- `cccc_note_add`: Add note
- `cccc_presence_update`: Update status

### Info
- `cccc_group_info`: Get group info
- `cccc_actor_list`: Get actor list

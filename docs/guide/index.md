# Introduction

CCCC (Collaborative Code Coordination Center) is a **local-first multi-agent collaboration kernel** that helps you coordinate multiple AI agents to work together on software projects.

## What is CCCC?

Think of CCCC as a "group chat for AI agents" with execution capabilities:

- **Working Groups**: Like IM group chats, but with durable history and automation
- **Multiple Agents**: Coordinate Claude Code, Codex, Droid, OpenCode, Copilot, and more
- **Web Control Plane**: Mobile-first responsive UI for remote access
- **IM-Grade Messaging**: @mentions, reply/quote, read receipts, and consistent behavior

## Key Concepts

### Working Group

A working group is the collaboration unit in CCCC. Each group has:

- An **append-only ledger** as the single source of truth
- One or more **actors** (agent sessions)
- Optional **scopes** (project directories)
- Automation rules for nudges, timeouts, etc.

### Actor

An actor is an agent session within a working group:

- **Foreman**: The first enabled actor becomes the coordinator + executor
- **Peer**: Additional actors serve as independent experts

Actors can run in two modes:
- **PTY (Terminal)**: Interactive terminal session with embedded xterm.js
- **Headless**: MCP-only mode for autonomous operation

### Ledger

The ledger is an append-only event stream that stores:
- All messages (user → agent, agent → agent)
- State changes (actor status, context updates)
- Decisions and outcomes

The daemon is the single writer to ensure consistency.

## Who Should Use CCCC?

CCCC is designed for:

- **Developers** who want to leverage multiple AI agents on a single project
- **Teams** who need to coordinate AI-assisted development
- **Power users** who want remote access to their AI agents from mobile devices

## Next Steps

- [Getting Started](/guide/getting-started/) - Set up CCCC in 10 minutes
- [Workflows](/guide/workflows) - Learn common collaboration patterns
- [Web UI](/guide/web-ui) - Master the control plane

// Shared type definitions for CCCC Web UI

export type GroupMeta = {
  group_id: string;
  title?: string;
  topic?: string;
  updated_at?: string;
  created_at?: string;
  running?: boolean;
};

export type GroupDoc = {
  group_id: string;
  title?: string;
  topic?: string;
  active_scope_key?: string;
  scopes?: Array<{ scope_key?: string; url?: string; label?: string }>;
};

export type LedgerEvent = {
  id?: string;
  ts?: string;
  kind?: string;
  by?: string;
  data?: any;
  _read_status?: Record<string, boolean>;
};

export type Actor = {
  id: string;
  role?: string;
  title?: string;
  enabled?: boolean;
  running?: boolean;  // Actual process running status
  command?: string[];
  runner?: string;
  runtime?: string;
  updated_at?: string;
  unread_count?: number;
};

export type RuntimeInfo = {
  name: string;
  display_name: string;
  command: string;
  available: boolean;
  path?: string;
  capabilities: string;
};

export type ReplyTarget = {
  eventId: string;
  by: string;
  text: string;
} | null;

export type GroupContext = {
  vision?: string;
  sketch?: string;
  milestones?: Array<{ id: string; title: string; status?: string; due?: string }>;
  tasks?: Array<{ id: string; title: string; status?: string; assignee?: string; milestone_id?: string }>;
  notes?: Array<{ id: string; title: string; content?: string }>;
  references?: Array<{ id: string; url: string; title?: string }>;
  presence?: Record<string, { status?: string; activity?: string; updated_at?: string }>;
};

export type GroupSettings = {
  nudge_after_seconds: number;
  actor_idle_timeout_seconds: number;
  keepalive_delay_seconds: number;
  keepalive_max_per_actor: number;
  silence_timeout_seconds: number;
  min_interval_seconds: number;
};

export type DirItem = { name: string; path: string; is_dir: boolean };
export type DirSuggestion = { name: string; path: string; icon: string };

// Runtime configuration
export const RUNTIME_DEFAULTS: Record<string, string> = {
  claude: "claude --dangerously-skip-permissions",
  codex: "codex --dangerously-bypass-approvals-and-sandbox",
  droid: "droid --auto high",
  opencode: "opencode",
  gemini: "gemini --yolo",
  copilot: "copilot --allow-all-tools",
  cursor: "cursor-agent",
  auggie: "auggie",
  kilocode: "kilocode",
};

export const RUNTIME_INFO: Record<string, { label: string; desc: string }> = {
  claude: { label: "Claude Code", desc: "Anthropic's Claude - strong coding" },
  codex: { label: "Codex CLI", desc: "OpenAI Codex - multimodal support" },
  droid: { label: "Droid", desc: "Robust auto mode, good for long sessions" },
  opencode: { label: "OpenCode", desc: "Solid coding CLI, no special env needed" },
  gemini: { label: "Gemini", desc: "Google Gemini - web search, large context" },
  copilot: { label: "GitHub Copilot", desc: "GitHub integrated, tool access" },
  cursor: { label: "Cursor Agent", desc: "Cursor AI - editor integrated" },
  auggie: { label: "Augment Code", desc: "Lightweight AI assistant" },
  kilocode: { label: "KiloCode", desc: "Autonomous coding capabilities" },
  custom: { label: "Custom", desc: "Enter your own command" },
};

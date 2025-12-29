// Shared type definitions for CCCC Web UI

// Theme types
export type Theme = "light" | "dark" | "system";

export type GroupMeta = {
  group_id: string;
  title?: string;
  topic?: string;
  updated_at?: string;
  created_at?: string;
  running?: boolean;
  state?: "active" | "idle" | "paused";
};

export type GroupDoc = {
  group_id: string;
  title?: string;
  topic?: string;
  active_scope_key?: string;
  scopes?: Array<{ scope_key?: string; url?: string; label?: string }>;
  state?: "active" | "idle" | "paused";
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

export type ProjectMdInfo = {
  found: boolean;
  path?: string | null;
  content?: string | null;
  error?: string | null;
};

export type GroupSettings = {
  nudge_after_seconds: number;
  actor_idle_timeout_seconds: number;
  keepalive_delay_seconds: number;
  keepalive_max_per_actor: number;
  silence_timeout_seconds: number;
  min_interval_seconds: number;
  standup_interval_seconds: number;
};

export type IMConfig = {
  platform?: "telegram" | "slack" | "discord";
  // Legacy single token field (backward compat)
  token_env?: string;
  token?: string;
  // Dual token fields for Slack (bot_token for outbound, app_token for inbound)
  bot_token_env?: string;
  app_token_env?: string;
};

export type IMStatus = {
  group_id: string;
  configured: boolean;
  platform?: string;
  running: boolean;
  pid?: number;
  subscribers: number;
};

export type DirItem = { name: string; path: string; is_dir: boolean };
export type DirSuggestion = { name: string; path: string; icon: string };

// Runtime configuration
export const RUNTIME_DEFAULTS: Record<string, string> = {
  claude: "claude --dangerously-skip-permissions",
  codex: "codex --dangerously-bypass-approvals-and-sandbox --search",
  droid: "droid --auto high",
  opencode: "opencode",
  copilot: "copilot --allow-all-tools --allow-all-paths",
};

export const RUNTIME_INFO: Record<string, { label: string; desc: string }> = {
  claude: { label: "Claude Code", desc: "" },
  codex: { label: "Codex CLI", desc: "" },
  droid: { label: "Droid", desc: "" },
  opencode: { label: "OpenCode", desc: "Manual MCP installation needed" },
  copilot: { label: "GitHub Copilot", desc: "Manual MCP installation needed" },
};

// Runtime colors for visual distinction
// Dark theme: semi-transparent dark backgrounds with bright text
// Light theme: semi-transparent light backgrounds with darker text
export const RUNTIME_COLORS: Record<string, { 
  // Dark theme
  bg: string; 
  text: string; 
  border: string; 
  dot: string;
  // Light theme
  bgLight: string;
  textLight: string;
  borderLight: string;
  dotLight: string;
}> = {
  claude: { 
    bg: "bg-orange-900/30", text: "text-orange-300", border: "border-orange-600/50", dot: "bg-orange-400",
    bgLight: "bg-orange-50", textLight: "text-orange-700", borderLight: "border-orange-300", dotLight: "bg-orange-500"
  },
  codex: { 
    bg: "bg-emerald-900/30", text: "text-emerald-300", border: "border-emerald-600/50", dot: "bg-emerald-400",
    bgLight: "bg-emerald-50", textLight: "text-emerald-700", borderLight: "border-emerald-300", dotLight: "bg-emerald-500"
  },
  droid: { 
    bg: "bg-violet-900/30", text: "text-violet-300", border: "border-violet-600/50", dot: "bg-violet-400",
    bgLight: "bg-violet-50", textLight: "text-violet-700", borderLight: "border-violet-300", dotLight: "bg-violet-500"
  },
  opencode: { 
    bg: "bg-cyan-900/30", text: "text-cyan-300", border: "border-cyan-600/50", dot: "bg-cyan-400",
    bgLight: "bg-cyan-50", textLight: "text-cyan-700", borderLight: "border-cyan-300", dotLight: "bg-cyan-500"
  },
  copilot: { 
    bg: "bg-slate-800/50", text: "text-slate-300", border: "border-slate-500/50", dot: "bg-slate-400",
    bgLight: "bg-gray-100", textLight: "text-gray-700", borderLight: "border-gray-300", dotLight: "bg-gray-500"
  },
  user: { 
    bg: "bg-sky-900/30", text: "text-sky-300", border: "border-sky-600/50", dot: "bg-sky-400",
    bgLight: "bg-sky-50", textLight: "text-sky-700", borderLight: "border-sky-300", dotLight: "bg-sky-500"
  },
};

// Helper to get runtime color, with fallback
export function getRuntimeColor(runtime?: string, isDark: boolean = true) {
  const colors = RUNTIME_COLORS[runtime || "codex"] || RUNTIME_COLORS.codex;
  if (isDark) {
    return {
      bg: colors.bg,
      text: colors.text,
      border: colors.border,
      dot: colors.dot,
    };
  }
  return {
    bg: colors.bgLight,
    text: colors.textLight,
    border: colors.borderLight,
    dot: colors.dotLight,
  };
}

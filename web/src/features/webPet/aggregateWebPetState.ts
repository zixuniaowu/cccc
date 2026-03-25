// Pure function: aggregate group context data into CatState + PanelData.
// Ported from desktop/src-tauri/src/state_aggregator.rs.

import type {
  GroupContext,
  AgentState,
  LedgerEvent,
} from "../../types";
import type {
  CatState,
  PanelData,
  AgentSummary,
} from "./types";

export interface AggregateInput {
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  sseStatus: "connected" | "connecting" | "disconnected";
  groupState?: string;
  teamName?: string;
  groupId?: string;
}

export interface AggregateOutput {
  catState: CatState;
  panelData: PanelData;
}

const MAX_SUMMARY_CHARS = 96;

export function truncateWebPetSummary(text: string): string {
  const cleaned = text.trim().replace(/\n/g, " ");
  if (cleaned.length <= MAX_SUMMARY_CHARS) return cleaned;
  return cleaned.slice(0, MAX_SUMMARY_CHARS - 1) + "…";
}

export function getChatMessageText(event: LedgerEvent): string {
  const data = event.data as Record<string, unknown> | undefined;
  return String(data?.text ?? "").trim();
}

function hasActivity(agent: AgentState): boolean {
  return !!(agent.hot?.active_task_id ?? "").trim();
}

export function aggregateWebPetState(input: AggregateInput): AggregateOutput {
  const {
    groupContext,
    sseStatus,
    groupState = "",
    teamName = "Team",
  } = input;
  void input.events;
  void input.groupId;

  const context = groupContext ?? {};
  const agentStates: AgentState[] = context.agent_states ?? [];

  // When group is inactive, treat all agents as idle
  const groupInactive = groupState === "paused" || groupState === "idle" || groupState === "stopped";

  // Count active agents (none when group is inactive)
  const activeAgents = groupInactive ? [] : agentStates.filter(hasActivity);

  // Determine cat state (priority: inactive > busy > working > napping)
  let catState: CatState;
  if (groupInactive) {
    catState = "napping";
  } else if (activeAgents.length >= 2) {
    catState = "busy";
  } else if (activeAgents.length === 1) {
    catState = "working";
  } else {
    catState = "napping";
  }

  // Build agent summaries
  const agents: AgentSummary[] = agentStates.map((agent) => ({
    id: agent.id,
    state: groupInactive
      ? "napping"
      : hasActivity(agent)
        ? activeAgents.length >= 2
          ? "busy"
          : "working"
        : "napping",
    focus: agent.hot?.focus ?? "",
  }));

  // Connection status from SSE
  const connected = sseStatus === "connected";
  // Return i18n key (webPet namespace) — translated at the component layer.
  const connectionMessage =
    sseStatus === "connected"
      ? "connectionConnected"
      : sseStatus === "connecting"
        ? "connectionConnecting"
        : "connectionDisconnected";

  // Extract task progress from tasks_summary.
  // Exclude archived from total so the progress bar reflects actionable tasks only.
  const tasksSummary = (context as GroupContext).tasks_summary;
  const taskProgress = tasksSummary
    ? {
        total: tasksSummary.total - (tasksSummary.archived ?? 0),
        done: tasksSummary.done,
        active: tasksSummary.active,
      }
    : undefined;

  return {
    catState,
    panelData: {
      teamName,
      agents,
      connection: {
        connected,
        message: connectionMessage,
      },
      taskProgress,
    },
  };
}

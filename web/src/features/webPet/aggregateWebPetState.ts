// Pure function: aggregate group context data into CatState + PanelData.
// Ported from desktop/src-tauri/src/state_aggregator.rs.

import type {
  GroupContext,
  AgentState,
  LedgerEvent,
  ObligationStatus,
} from "../../types";
import type {
  CatState,
  PanelData,
  AgentSummary,
  ActionItem,
} from "./types";

export interface AggregateInput {
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  sseStatus: "connected" | "connecting" | "disconnected";
  groupState?: string;
  teamName?: string;
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

function displayActor(actorId: string): string {
  const trimmed = actorId.trim();
  return trimmed || "system";
}

export function getChatMessageText(event: LedgerEvent): string {
  const data = event.data as Record<string, unknown> | undefined;
  return String(data?.text ?? "").trim();
}

export function isPendingReplyRequiredForUser(event: LedgerEvent): boolean {
  if (event.kind !== "chat.message") return false;

  const obligationStatus = event._obligation_status;
  if (!obligationStatus) return false;

  const userStatus: ObligationStatus | undefined = obligationStatus["user"];
  if (!userStatus) return false;

  return !!(
    userStatus.reply_required &&
    !userStatus.acked &&
    !userStatus.replied
  );
}

function hasActivity(agent: AgentState): boolean {
  return !!(agent.hot?.active_task_id ?? "").trim();
}

function collectWaitingUserTasks(
  context: GroupContext
): ActionItem[] {
  const attention = context.attention;
  const waitingUser = attention?.waiting_user;

  // If attention.waiting_user is an array of tasks/entries, use it
  if (Array.isArray(waitingUser) && waitingUser.length > 0) {
    return waitingUser.map((entry) => {
      if (typeof entry === "string") {
      return {
        id: `${entry}_waiting_on_user`,
        agent: "system",
        summary: entry,
      };
      }
      const task = entry;
      return {
        id: `${task.id || "unknown"}_waiting_on_user`,
        agent: displayActor(task.assignee || ""),
        summary: truncateWebPetSummary(task.title || ""),
      };
    });
  }

  // Fallback: scan coordination tasks for waiting_on=user
  const tasks = context.coordination?.tasks ?? [];
  return tasks
    .filter((task) => {
      const waitingOn = (task.waiting_on ?? "").toLowerCase();
      const status = (task.status ?? "").toLowerCase();
      return waitingOn === "user" && status !== "done" && status !== "archived";
    })
    .map((task) => ({
      id: `${task.id || "unknown"}_waiting_on_user`,
      agent: displayActor(task.assignee || ""),
      summary: truncateWebPetSummary(task.title || ""),
    }));
}

function collectPendingReplyRequired(
  events: LedgerEvent[]
): ActionItem[] {
  const items: ActionItem[] = [];

  for (const event of events) {
    if (!isPendingReplyRequiredForUser(event)) continue;

    const text = getChatMessageText(event);
    if (!text) continue;

    items.push({
      id: event.id || "unknown",
      agent: displayActor(event.by || ""),
      summary: truncateWebPetSummary(text),
    });
  }

  return items;
}

export function aggregateWebPetState(input: AggregateInput): AggregateOutput {
  const {
    groupContext,
    events,
    sseStatus,
    groupState = "",
    teamName = "Team",
  } = input;

  const context = groupContext ?? {};
  const agentStates: AgentState[] = context.agent_states ?? [];

  // Collect action items
  const waitingUserItems = collectWaitingUserTasks(context as GroupContext);
  const replyRequiredItems = collectPendingReplyRequired(events);
  const actionItems = [...waitingUserItems, ...replyRequiredItems];

  // Count active agents
  const activeAgents = agentStates.filter(hasActivity);

  // Determine cat state (priority: paused > needs_you > busy > working > napping)
  let catState: CatState;
  if (groupState === "paused") {
    catState = "napping";
  } else if (actionItems.length > 0) {
    catState = "needs_you";
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
    state: hasActivity(agent)
      ? activeAgents.length >= 2
        ? "busy"
        : "working"
      : "napping",
    focus: agent.hot?.focus ?? "",
  }));

  // Add "attention" pseudo-agent if needs_you but no agent has that state
  if (
    catState === "needs_you" &&
    !agents.some((a) => a.state === "needs_you")
  ) {
    const first = actionItems[0];
    if (first) {
      agents.push({
        id: "attention",
        state: "needs_you",
        focus: first.summary,
      });
    }
  }

  // Limit action items to top 3
  const topActionItems = actionItems.slice(0, 3);

  // Connection status from SSE
  const connected = sseStatus === "connected";
  const connectionMessage =
    sseStatus === "connected"
      ? "Connected"
      : sseStatus === "connecting"
        ? "Connecting..."
        : "Disconnected";

  return {
    catState,
    panelData: {
      teamName,
      agents,
      actionItems: topActionItems,
      connection: {
        connected,
        message: connectionMessage,
      },
    },
  };
}

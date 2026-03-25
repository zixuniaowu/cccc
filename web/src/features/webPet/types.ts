// Web Pet type definitions

export type CatState = "napping" | "working" | "busy";

export const CAT_STATES: readonly CatState[] = [
  "napping",
  "working",
  "busy",
] as const;

export interface AgentSummary {
  id: string;
  state: string;
  focus: string;
}

export type ReminderKind =
  | "reply_required"
  | "actor_down"
  | "mention";

export type ReminderAction =
  | {
      type: "send_suggestion";
      groupId: string;
      text: string;
      to?: string[];
      replyTo?: string;
    }
  | {
      type: "restart_actor";
      groupId: string;
      actorId: string;
    };

export interface PetReminder {
  id: string;
  kind: ReminderKind;
  priority: number;
  summary: string;
  suggestion?: string;
  suggestionPreview?: string;
  agent: string;
  ephemeral?: boolean;
  source: {
    eventId?: string;
    taskId?: string;
    actorId?: string;
  };
  fingerprint: string;
  action: ReminderAction;
}

export interface ConnectionStatus {
  connected: boolean;
  message: string;
}

export interface PanelData {
  teamName: string;
  agents: AgentSummary[];
  connection: ConnectionStatus;
  taskProgress?: {
    total: number;
    done: number;
    active: number;
  };
}

export type WebPetSpriteUrls = Record<CatState, string>;

export type PetReaction = {
  kind: "mention" | "success" | "error";
  durationMs: number;
  reason: string;
  source?: {
    eventId?: string;
    taskId?: string;
  };
} | null;

export interface CatEngineOptions {
  canvas: HTMLCanvasElement;
  spriteUrls: WebPetSpriteUrls;
}

export interface CatEngine {
  load(): Promise<void>;
  setState(state: CatState): void;
  setHint(hint: string): void;
  playReaction(kind: "mention" | "success" | "error"): void;
  destroy(): void;
}

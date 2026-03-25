import { useEffect, useRef } from "react";
import type { Actor, GroupContext, Task } from "../../types";
import { contextSync, restartActor, type ApiResponse } from "../../services/api";
import { derivePetPeerActions, type PetPeerAction } from "./petPeerPolicy";
import type { PetPersonaPolicy } from "./petPersona";

const ACTION_COOLDOWN_MS = 60 * 1000;

function assertPetPeerActionOk(response: ApiResponse<unknown>) {
  if (!response.ok) {
    throw new Error(String(response.error?.message || "pet peer action failed"));
  }
}

export async function runPetPeerAction(input: {
  action: PetPeerAction;
  groupId: string;
  cooldownKey: string;
  cooldownRef: { current: Record<string, number> };
  inflightRef: { current: Set<string> };
}) {
  try {
    if (input.action.kind === "restart_actor") {
      assertPetPeerActionOk(await restartActor(input.groupId, input.action.actorId));
      return;
    }
    assertPetPeerActionOk(await contextSync(input.groupId, [
      { op: "task.move", task_id: input.action.taskId, status: "done" },
    ]));
  } catch {
    input.cooldownRef.current[input.cooldownKey] = 0;
  } finally {
    input.inflightRef.current.delete(input.cooldownKey);
  }
}

export function usePetPeerActions(input: {
  enabled: boolean;
  groupId: string | null | undefined;
  groupState?: string | null;
  actors: Actor[];
  groupContext: GroupContext | null;
  policy: PetPersonaPolicy;
}) {
  const cooldownRef = useRef<Record<string, number>>({});
  const inflightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const groupId = String(input.groupId || "").trim();
    if (!input.enabled || !groupId) return;

    const tasks: Task[] = input.groupContext?.coordination?.tasks ?? [];
    const actions = derivePetPeerActions({
      actors: input.actors,
      tasks,
      groupState: input.groupState,
    }).filter((action) => {
      if (action.kind === "restart_actor") return input.policy.autoRestartActors;
      if (action.kind === "complete_task") return input.policy.autoCompleteTasks;
      return false;
    });

    const now = Date.now();
    for (const action of actions) {
      const key =
        action.kind === "restart_actor"
          ? `restart:${groupId}:${action.actorId}`
          : `complete:${groupId}:${action.taskId}`;
      const cooldownUntil = cooldownRef.current[key] || 0;
      if (cooldownUntil > now || inflightRef.current.has(key)) continue;

      inflightRef.current.add(key);
      cooldownRef.current[key] = now + ACTION_COOLDOWN_MS;

      void runPetPeerAction({
        action,
        groupId,
        cooldownKey: key,
        cooldownRef,
        inflightRef,
      });
    }
  }, [input.actors, input.enabled, input.groupContext, input.groupId, input.groupState, input.policy.autoCompleteTasks, input.policy.autoRestartActors]);
}

import type { Actor, Task } from "../../types";

export type PetPeerAction =
  | { kind: "restart_actor"; actorId: string }
  | { kind: "complete_task"; taskId: string };

function normalizeStatus(value: string | null | undefined): string {
  return String(value || "").trim().toLowerCase();
}

function checklistDone(task: Task): boolean {
  const items = Array.isArray(task.checklist) ? task.checklist : [];
  return items.every((item) => normalizeStatus(item.status) === "done");
}

function stepsDone(task: Task): boolean {
  const steps = Array.isArray(task.steps) ? task.steps : [];
  return steps.every((step) => {
    const status = normalizeStatus(step.status);
    return !status || status === "done";
  });
}

export function shouldPetRestartActor(
  actor: Actor,
  groupState: string | null | undefined,
): boolean {
  const state = normalizeStatus(groupState);
  if (state === "paused" || state === "idle" || state === "stopped") return false;
  if (String(actor.id || "").trim() === "user") return false;
  return actor.enabled === true && actor.running === false;
}

export function shouldPetCompleteTask(task: Task): boolean {
  const status = normalizeStatus(task.status);
  if (status !== "active") return false;
  if (normalizeStatus(task.waiting_on) !== "none") return false;
  if (Array.isArray(task.blocked_by) && task.blocked_by.length > 0) return false;
  if (String(task.handoff_to || "").trim()) return false;

  const hasOutcome = String(task.outcome || "").trim().length > 0;
  const progress = Number(task.progress ?? 0);
  const doneByProgress = Number.isFinite(progress) && progress >= 1;

  if (!hasOutcome && !doneByProgress) return false;
  if (!checklistDone(task)) return false;
  if (!stepsDone(task)) return false;

  return true;
}

export function derivePetPeerActions(input: {
  actors: Actor[];
  tasks: Task[];
  groupState?: string | null;
}): PetPeerAction[] {
  const actions: PetPeerAction[] = [];

  for (const actor of input.actors) {
    if (shouldPetRestartActor(actor, input.groupState)) {
      actions.push({ kind: "restart_actor", actorId: actor.id });
    }
  }

  for (const task of input.tasks) {
    if (shouldPetCompleteTask(task)) {
      actions.push({ kind: "complete_task", taskId: task.id });
    }
  }

  return actions;
}

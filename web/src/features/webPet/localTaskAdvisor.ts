import type { PetReminder } from "./types";
import type { GroupContext } from "../../types";
import { buildTaskAdvisorEvidence } from "./taskAdvisor/buildEvidence";
import { indexTaskAdvisorContext } from "./taskAdvisor/indexContext";
import { renderTaskProposalReminder } from "./taskAdvisor/renderProposal";
import { taskAdvisorRules } from "./taskAdvisor/rules";
import type { TaskProposalCandidate } from "./taskAdvisor/types";
import type { TaskProposalStylePolicy } from "./types";

export function buildLocalTaskProposalReminders(
  groupId: string,
  groupContext: GroupContext | null,
  style?: TaskProposalStylePolicy,
): PetReminder[] {
  const context = indexTaskAdvisorContext({ groupId, groupContext });
  if (!context) return [];

  const bestByTask = new Map<string, TaskProposalCandidate>();
  for (const evidence of buildTaskAdvisorEvidence(context)) {
    for (const rule of taskAdvisorRules) {
      const candidate = rule(evidence);
      if (!candidate) continue;
      const previous = bestByTask.get(candidate.taskId);
      if (!previous || candidate.priority > previous.priority) {
        bestByTask.set(candidate.taskId, candidate);
      }
    }
  }

  return Array.from(bestByTask.values())
    .sort((left, right) => right.priority - left.priority)
    .map((candidate) => renderTaskProposalReminder(candidate, style));
}

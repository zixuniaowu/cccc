import { useMemo } from "react";

import type { Actor, GroupDoc, GroupMeta, GroupRuntimeStatus } from "../types";
import { getGroupRuntimeStatus } from "../utils/groupStatus";

type UseSelectedGroupRuntimeArgs = {
  groups: GroupMeta[];
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  actors: Actor[];
};

type SelectedGroupRuntime = {
  selectedGroupMeta: GroupMeta | null;
  selectedGroupRunning: boolean;
  selectedGroupRuntimeStatus: GroupRuntimeStatus;
  orderedSelectedGroupPatch: Pick<GroupMeta, "running" | "state" | "runtime_status"> | null;
};

export function computeSelectedGroupRuntime({
  groups,
  selectedGroupId,
  groupDoc,
  actors,
}: UseSelectedGroupRuntimeArgs): SelectedGroupRuntime {
  const selectedGroupMeta = groups.find((group) => String(group.group_id || "") === selectedGroupId) || null;
  const metaRuntime = getGroupRuntimeStatus(selectedGroupMeta);
  const docRuntime = getGroupRuntimeStatus(groupDoc);
  const runtimeRunning =
    docRuntime.runtime_running || metaRuntime.runtime_running || actors.some((actor) => !!actor.running);

  const selectedGroupRuntimeStatus = {
    ...metaRuntime,
    ...docRuntime,
    runtime_running: runtimeRunning,
  };

  return {
    selectedGroupMeta,
    selectedGroupRunning: selectedGroupRuntimeStatus.runtime_running,
    selectedGroupRuntimeStatus,
    orderedSelectedGroupPatch: selectedGroupId
      ? {
          running: selectedGroupRuntimeStatus.runtime_running,
          state: selectedGroupRuntimeStatus.lifecycle_state as GroupMeta["state"],
          runtime_status: selectedGroupRuntimeStatus,
        }
      : null,
  };
}

export function useSelectedGroupRuntime(args: UseSelectedGroupRuntimeArgs): SelectedGroupRuntime {
  return useMemo(() => computeSelectedGroupRuntime(args), [args]);
}

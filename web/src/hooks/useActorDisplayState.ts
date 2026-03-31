import { useMemo } from "react";

import { getTerminalSignalKey, useTerminalSignalsStore } from "../stores";
import type { Actor } from "../types";
import { getActorDisplayWorkingState } from "../utils/terminalWorkingState";
import { getActorTabIndicatorState, type ActorTabIndicator } from "../components/tabBarIndicator";

export type ActorDisplayState = {
  isRunning: boolean;
  assumeRunning: boolean;
  workingState: string;
  indicator: ActorTabIndicator;
};

type UseActorDisplayStateInput = {
  groupId: string;
  actor: Actor;
  selectedGroupRunning?: boolean;
  selectedGroupActorsHydrating?: boolean;
};

export function useActorDisplayState({
  groupId,
  actor,
  selectedGroupRunning = false,
  selectedGroupActorsHydrating = false,
}: UseActorDisplayStateInput): ActorDisplayState {
  const terminalSignal = useTerminalSignalsStore((state) => state.signals[getTerminalSignalKey(groupId, actor.id)]);

  return useMemo(() => {
    const runningKnown = typeof actor.running === "boolean";
    const isRunning = runningKnown ? actor.running : (actor.enabled ?? false);
    const assumeRunning = !runningKnown && selectedGroupRunning && selectedGroupActorsHydrating && actor.enabled !== false;
    const workingState = getActorDisplayWorkingState(actor, terminalSignal);
    const indicator = getActorTabIndicatorState({
      isRunning: Boolean(isRunning),
      workingState,
      assumeRunning,
    });

    return {
      isRunning: Boolean(isRunning),
      assumeRunning,
      workingState,
      indicator,
    };
  }, [actor, selectedGroupActorsHydrating, selectedGroupRunning, terminalSignal]);
}

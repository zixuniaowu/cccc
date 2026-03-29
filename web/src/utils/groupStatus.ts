import { getGroupPresenceDotClass } from "./statusIndicators";

export type GroupStatusKey = "run" | "paused" | "idle" | "stop";

export type GroupStatus = {
  key: GroupStatusKey;
  label: string;
  pillClass: string;
  dotClass: string;
};

function buildStatus(key: GroupStatusKey, label: string, dotClass: string): GroupStatus {
  return {
    key,
    label,
    pillClass: `glass-status-pill glass-status-pill-${key}`,
    dotClass,
  };
}

export function getGroupStatus(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", "STOP", getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", "PAUSED", getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", "IDLE", getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", "RUN", getGroupPresenceDotClass("run"));
}

export function getGroupStatusLight(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", "STOP", getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", "PAUSED", getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", "IDLE", getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", "RUN", getGroupPresenceDotClass("run"));
}

/** Unified group status using dark: prefix - no isDark dependency needed */
export function getGroupStatusUnified(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", "STOP", getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", "PAUSED", getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", "IDLE", getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", "RUN", getGroupPresenceDotClass("run"));
}

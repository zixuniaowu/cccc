export type GroupStatus = { label: string; pillClass: string; dotClass: string };

export function getGroupStatus(running: boolean, state?: string): GroupStatus {
  switch (state) {
    case "paused":
      return {
        label: "⏸ PAUSED",
        pillClass: "bg-amber-500/20 text-amber-500",
        dotClass: "bg-amber-400",
      };
    case "idle":
      return {
        label: "✓ IDLE",
        pillClass: "bg-blue-500/20 text-blue-400",
        dotClass: "bg-blue-400",
      };
    default:
      break;
  }
  if (!running) {
    return {
      label: "○ STOP",
      pillClass: "bg-slate-700/50 text-slate-500",
      dotClass: "bg-slate-500",
    };
  }
  return {
    label: "● RUN",
    pillClass: "bg-emerald-500/20 text-emerald-500",
    dotClass: "bg-emerald-400",
  };
}

export function getGroupStatusLight(running: boolean, state?: string): GroupStatus {
  switch (state) {
    case "paused":
      return {
        label: "⏸ PAUSED",
        pillClass: "bg-amber-100 text-amber-600",
        dotClass: "bg-amber-500",
      };
    case "idle":
      return {
        label: "✓ IDLE",
        pillClass: "bg-blue-100 text-blue-600",
        dotClass: "bg-blue-500",
      };
    default:
      break;
  }
  if (!running) {
    return {
      label: "○ STOP",
      pillClass: "bg-gray-200 text-gray-500",
      dotClass: "bg-gray-400",
    };
  }
  return {
    label: "● RUN",
    pillClass: "bg-emerald-100 text-emerald-600",
    dotClass: "bg-emerald-500",
  };
}

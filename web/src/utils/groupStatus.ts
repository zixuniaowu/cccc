export function getGroupStatus(running: boolean, state?: string): { label: string; colorClass: string } {
  if (!running) {
    return { label: "○ STOP", colorClass: "bg-slate-700/50 text-slate-500" };
  }
  switch (state) {
    case "paused":
      return { label: "⏸ PAUSED", colorClass: "bg-amber-500/20 text-amber-500" };
    case "idle":
      return { label: "✓ IDLE", colorClass: "bg-blue-500/20 text-blue-400" };
    default:
      return { label: "● RUN", colorClass: "bg-emerald-500/20 text-emerald-500" };
  }
}

export function getGroupStatusLight(running: boolean, state?: string): { label: string; colorClass: string } {
  if (!running) {
    return { label: "○ STOP", colorClass: "bg-gray-200 text-gray-500" };
  }
  switch (state) {
    case "paused":
      return { label: "⏸ PAUSED", colorClass: "bg-amber-100 text-amber-600" };
    case "idle":
      return { label: "✓ IDLE", colorClass: "bg-blue-100 text-blue-600" };
    default:
      return { label: "● RUN", colorClass: "bg-emerald-100 text-emerald-600" };
  }
}


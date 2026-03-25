type SnapshotTranslate = (
  key: string,
  fallback: string,
  vars?: Record<string, unknown>,
) => string;

const TASKS_PATTERN =
  /^Tasks:\s*total=(\d+),\s*active=(\d+),\s*done=(\d+),\s*archived=(\d+)$/i;

export function formatPetSnapshotLine(
  line: string,
  tr: SnapshotTranslate,
): string {
  const trimmed = String(line || "").trim();
  if (!trimmed) return "";

  if (trimmed.startsWith("Group: ")) {
    return tr("snapshot.group", "Group: {{value}}", {
      value: trimmed.slice("Group: ".length).trim(),
    });
  }

  if (trimmed.startsWith("Group State: ")) {
    return tr("snapshot.groupState", "Group State: {{value}}", {
      value: trimmed.slice("Group State: ".length).trim(),
    });
  }

  const tasksMatch = trimmed.match(TASKS_PATTERN);
  if (tasksMatch) {
    return tr(
      "snapshot.tasks",
      "Tasks: total={{total}}, active={{active}}, done={{done}}, archived={{archived}}",
      {
        total: Number(tasksMatch[1] || 0),
        active: Number(tasksMatch[2] || 0),
        done: Number(tasksMatch[3] || 0),
        archived: Number(tasksMatch[4] || 0),
      },
    );
  }

  if (trimmed.startsWith("Agent Snapshot: ")) {
    return tr("snapshot.agentSnapshot", "Agent Snapshot: {{value}}", {
      value: trimmed.slice("Agent Snapshot: ".length).trim(),
    });
  }

  return trimmed;
}

export function formatPetSnapshot(
  snapshot: string,
  tr: SnapshotTranslate,
): string {
  return String(snapshot || "")
    .split(/\r?\n/u)
    .map((line) => formatPetSnapshotLine(line, tr))
    .filter(Boolean)
    .join("\n");
}

export function getPetSnapshotHeadline(
  snapshot: string,
  tr: SnapshotTranslate,
): string {
  const firstLine = String(snapshot || "")
    .split(/\r?\n/u)
    .find((line) => String(line || "").trim());
  return firstLine ? formatPetSnapshotLine(firstLine, tr) : "";
}

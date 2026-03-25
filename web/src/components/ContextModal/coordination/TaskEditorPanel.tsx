import type { Dispatch, SetStateAction } from "react";
import type { Task, TaskWaitingOn } from "../../../types";
import { formatFullTime, formatTime } from "../../../utils/time";
import { classNames } from "../../../utils/classNames";
import { type TaskWorkflowScaffoldId } from "../../../utils/taskWorkflow";
import {
  statusTone,
  type ContextTranslator,
  type TaskDeleteInfo,
  type TaskDraft,
  WAITING_ON_OPTIONS,
} from "../model";
import type { ContextModalUi } from "../ui";

interface WorkflowCoverage {
  isRoot: boolean;
  isOptimization: boolean;
  needsContract: boolean;
  needsCloseout: boolean;
  hasGoal: boolean;
  hasSuccessCriteria: boolean;
  hasRequiredEvidence: boolean;
  hasOwner: boolean;
  hasBaseline: boolean;
  hasPrimaryMetric: boolean;
  hasVerifierBoundary: boolean;
  hasOutcomeSummary: boolean;
  hasCloseoutVerdict: boolean;
  hasVerificationSummary: boolean;
  hasAttemptDecision: boolean;
  missingCloseout: string[];
  missingSetup: string[];
}

interface TaskScaffold {
  description: string;
  notes: string;
  checklist: string;
}

interface TaskEditorPanelProps {
  tr: ContextTranslator;
  ui: ContextModalUi;
  taskEditorMode: "none" | "create" | "edit";
  taskDraft: TaskDraft | null;
  hasTaskUnsaved: boolean;
  syncBusy: boolean;
  selectedTask: Task | null;
  selectedTaskDeleteInfo: TaskDeleteInfo;
  selectedTaskDeleteHint: string;
  taskWorkflowCoverage: WorkflowCoverage;
  taskScaffoldId: TaskWorkflowScaffoldId;
  selectedTaskScaffold: TaskScaffold;
  setTaskDraft: Dispatch<SetStateAction<TaskDraft | null>>;
  onTaskScaffoldIdChange: (value: TaskWorkflowScaffoldId) => void;
  onApplyTaskScaffold: () => void;
  onResetTask: () => void;
  onClose: () => void;
  onDeleteSelectedTask: () => void;
  onSaveTask: () => void;
}

export function TaskEditorPanel({
  tr,
  ui,
  taskEditorMode,
  taskDraft,
  hasTaskUnsaved,
  syncBusy,
  selectedTask,
  selectedTaskDeleteInfo,
  selectedTaskDeleteHint,
  taskWorkflowCoverage,
  taskScaffoldId,
  selectedTaskScaffold,
  setTaskDraft,
  onTaskScaffoldIdChange,
  onApplyTaskScaffold,
  onResetTask,
  onClose,
  onDeleteSelectedTask,
  onSaveTask,
}: TaskEditorPanelProps) {
  if (taskEditorMode === "none" || !taskDraft) {
    return null;
  }

  const isCreate = taskEditorMode === "create";
  const workflowToneClass = (ok: boolean) => classNames(
    "rounded-full px-2 py-0.5 text-[11px] font-medium",
    ok
      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
      : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
  );
  const workflowSummary = taskWorkflowCoverage.needsCloseout
    ? tr(
      "context.taskWorkflowNeedsCloseout",
      "Closeout is still missing {{items}}.",
      { items: taskWorkflowCoverage.missingCloseout.join(", ") }
    )
    : taskWorkflowCoverage.needsContract
      ? tr(
        "context.taskWorkflowNeedsContract",
        "This task contract is still missing {{items}}.",
        { items: taskWorkflowCoverage.missingSetup.join(", ") }
      )
      : taskWorkflowCoverage.isOptimization
        ? tr(
          "context.taskWorkflowOptimizationReady",
          "This optimization task has a usable baseline / metric contract."
        )
        : taskWorkflowCoverage.isRoot
          ? tr("context.taskWorkflowRootReady", "This root task has a usable goal / evidence contract.")
          : tr("context.taskWorkflowLeanReady", "Keep subtasks lean unless more workflow structure adds clarity.");

  return (
    <section className={classNames(ui.surfaceClass, "p-4")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className={classNames("text-sm font-semibold", "text-[var(--color-text-primary)]")}>{isCreate ? tr("context.newTask", "New task") : tr("context.taskDetails", "Task editor")}</div>
          <div className={classNames("mt-1 text-xs", ui.mutedTextClass)}>{isCreate ? tr("context.newTaskHint", "Create a new shared task. Keep it lean, then apply a scaffold only when the workflow needs sharper control.") : (selectedTask?.id || "")}</div>
        </div>
        <div className="flex items-center gap-2">
          {hasTaskUnsaved ? <span className={classNames("rounded-full px-2 py-0.5 text-[11px] font-medium", "bg-amber-500/15 text-amber-600 dark:text-amber-400")}>{tr("context.unsaved", "Unsaved")}</span> : null}
          <button type="button" onClick={onResetTask} disabled={syncBusy} className={ui.buttonSecondaryClass}>{isCreate ? tr("context.clear", "Clear") : tr("context.reset", "Reset")}</button>
          <button type="button" onClick={onClose} className={ui.buttonSecondaryClass}>{tr("context.close", "Close")}</button>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className={classNames("text-xs", ui.mutedTextClass)}>
            {isCreate ? tr("context.editorInlineHint", "Editing opens in a side panel so the board stays readable.") : (selectedTask?.updated_at ? `${tr("context.updated", "Updated {{time}}", { time: formatTime(selectedTask.updated_at) })}` : tr("context.notUpdatedYet", "Not updated yet"))}
          </div>
          <span className={classNames("rounded-full border px-2 py-0.5 text-[11px] font-medium", statusTone(taskDraft.status))}>{taskDraft.status}</span>
        </div>

        <div className={classNames("rounded-xl border px-3 py-3", "glass-panel")}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 flex-1">
              <div className={classNames("text-sm font-semibold", "text-[var(--color-text-primary)]")}>{tr("context.taskWorkflow", "Workflow scaffold")}</div>
              <div className={classNames("mt-1 text-xs", ui.mutedTextClass)}>{selectedTaskScaffold.description}</div>
            </div>

            <div className="grid gap-2 sm:w-[15rem]">
              <select value={taskScaffoldId} onChange={(event) => onTaskScaffoldIdChange(event.target.value as TaskWorkflowScaffoldId)} className={ui.inputClass}>
                {[
                  ["lean", tr("context.taskScaffoldLean", "Lean task")],
                  ["root", tr("context.taskScaffoldRoot", "Root task")],
                  ["planner", tr("context.taskScaffoldPlanner", "Planner")],
                  ["reviewer", tr("context.taskScaffoldReviewer", "Reviewer")],
                  ["debugger", tr("context.taskScaffoldDebugger", "Debugger")],
                  ["release", tr("context.taskScaffoldRelease", "Release")],
                  ["optimization", tr("context.taskScaffoldOptimization", "Optimization")],
                ].map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <button type="button" onClick={onApplyTaskScaffold} disabled={syncBusy} className={ui.buttonSecondaryClass}>
                {tr("context.applyScaffold", "Apply scaffold")}
              </button>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap gap-1.5">
            <span className={classNames("rounded-full px-2 py-0.5 text-[11px] font-medium", "glass-panel text-[var(--color-text-secondary)]")}>
              {taskWorkflowCoverage.isRoot ? tr("context.rootTask", "Root task") : tr("context.subtask", "Subtask")}
            </span>
            {taskWorkflowCoverage.isRoot || taskWorkflowCoverage.isOptimization ? (
              <>
                <span className={workflowToneClass(taskWorkflowCoverage.hasGoal)}>{tr("context.goal", "Goal")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasSuccessCriteria)}>{tr("context.successCriteria", "Success criteria")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasRequiredEvidence)}>{tr("context.requiredEvidence", "Required evidence")}</span>
              </>
            ) : null}
            {taskWorkflowCoverage.isRoot ? (
              <span className={workflowToneClass(taskWorkflowCoverage.hasOwner)}>{tr("context.owner", "Owner")}</span>
            ) : null}
            {taskWorkflowCoverage.isOptimization ? (
              <>
                <span className={workflowToneClass(taskWorkflowCoverage.hasBaseline)}>{tr("context.baseline", "Baseline")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasPrimaryMetric)}>{tr("context.primaryMetric", "Primary metric")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasVerifierBoundary)}>{tr("context.verifierBoundary", "Verifier boundary")}</span>
              </>
            ) : null}
            {String(taskDraft.status || "").toLowerCase() === "done" ? (
              <>
                <span className={workflowToneClass(taskWorkflowCoverage.hasOutcomeSummary)}>{tr("context.outcomeSummary", "Outcome summary")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasCloseoutVerdict)}>{tr("context.closeoutVerdict", "Closeout verdict")}</span>
                <span className={workflowToneClass(taskWorkflowCoverage.hasVerificationSummary)}>{tr("context.verificationSummary", "Verification summary")}</span>
                {taskWorkflowCoverage.isOptimization ? (
                  <span className={workflowToneClass(taskWorkflowCoverage.hasAttemptDecision)}>{tr("context.attemptDecision", "Attempt decision")}</span>
                ) : null}
              </>
            ) : null}
          </div>

          <div className={classNames(
            "mt-3 rounded-lg border px-3 py-2 text-xs",
            taskWorkflowCoverage.needsContract || taskWorkflowCoverage.needsCloseout
              ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
              : "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          )}>
            {workflowSummary}
          </div>
        </div>

        <label className="block text-sm">
          <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.titleField", "Title")}</span>
          <input value={taskDraft.title} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, title: event.target.value } : prev)} className={ui.inputClass} />
        </label>

        <label className="block text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.outcome", "Outcome")}</span>
            <span className={classNames("mb-1 block text-[11px]", ui.mutedTextClass)}>{tr("context.outcomeHint", "Use this as the concise result summary, especially at closeout.")}</span>
          </div>
          <textarea value={taskDraft.outcome} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, outcome: event.target.value } : prev)} className={ui.textareaClass} />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.status", "Status")}</span>
            <select value={taskDraft.status} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, status: event.target.value } : prev)} className={ui.inputClass}>
              <option value="planned">{tr("context.planned", "Planned")}</option>
              <option value="active">{tr("context.active", "Active")}</option>
              <option value="done">{tr("context.done", "Done")}</option>
              <option value="archived">{tr("context.archived", "Archived")}</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.assignee", "Assignee")}</span>
            <input value={taskDraft.assignee} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, assignee: event.target.value } : prev)} className={ui.inputClass} />
          </label>
        </div>

        <label className="block text-sm">
          <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.priority", "Priority")}</span>
          <input value={taskDraft.priority} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, priority: event.target.value } : prev)} className={ui.inputClass} />
        </label>

        <div className="grid gap-3">
          <label className="block text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.notes", "Notes")}</span>
              <span className={classNames("mb-1 block text-[11px]", ui.mutedTextClass)}>{tr("context.notesHint", "Keep the task contract, evidence requirements, and closeout structure here.")}</span>
            </div>
            <textarea value={taskDraft.notes} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, notes: event.target.value } : prev)} className={classNames(ui.textareaClass, "min-h-[220px]")} />
          </label>

          <label className="block text-sm">
            <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.checklist", "Checklist")}</span>
            <textarea value={taskDraft.checklist} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, checklist: event.target.value } : prev)} className={classNames(ui.textareaClass, "min-h-[140px]")} placeholder={tr("context.checklistPlaceholder", "Use [ ], [~], [x] prefixes if useful.")} />
          </label>
        </div>

        <details className={classNames("rounded-xl border px-3 py-3", "glass-card") }>
          <summary className={classNames("cursor-pointer text-sm font-medium", "text-[var(--color-text-primary)]")}>{tr("context.advancedTaskFields", "Advanced details")}</summary>
          <div className="mt-3 space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.parentTask", "Parent task")}</span>
                <input value={taskDraft.parentId} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, parentId: event.target.value } : prev)} className={ui.inputClass} placeholder={tr("context.rootTask", "Root task")} />
              </label>
              <label className="block text-sm">
                <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.handoffTo", "Handoff to")}</span>
                <input value={taskDraft.handoffTo} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, handoffTo: event.target.value } : prev)} className={ui.inputClass} />
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.blockedBy", "Blocked by")}</span>
                <textarea value={taskDraft.blockedBy} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, blockedBy: event.target.value } : prev)} className={classNames(ui.textareaClass, "min-h-[90px]")} placeholder={tr("context.onePerLine", "One per line")} />
              </label>
              <label className="block text-sm">
                <span className={classNames("mb-1 block text-xs font-medium uppercase tracking-wide", ui.mutedTextClass)}>{tr("context.waitingOn", "Waiting on")}</span>
                <select value={taskDraft.waitingOn} onChange={(event) => setTaskDraft((prev) => prev ? { ...prev, waitingOn: event.target.value as TaskWaitingOn } : prev)} className={ui.inputClass}>
                  {WAITING_ON_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
            </div>
          </div>
        </details>

        {isCreate ? (
          <div className={classNames("rounded-xl border px-3 py-3 text-xs", "glass-card text-[var(--color-text-muted)]")}>
            {tr("context.newTaskHint", "Create a new shared task. Keep it lean, then apply a scaffold only when the workflow needs sharper control.")}
          </div>
        ) : selectedTask ? (
          <div className={classNames("grid gap-2 rounded-xl border px-3 py-3 text-xs sm:grid-cols-2", "glass-card text-[var(--color-text-muted)]")}>
            <div>{tr("context.createdAt", "Created")}: {selectedTask.created_at ? formatFullTime(selectedTask.created_at) : "-"}</div>
            <div>{tr("context.updatedAt", "Updated")}: {selectedTask.updated_at ? formatFullTime(selectedTask.updated_at) : "-"}</div>
            <div>{tr("context.archivedFrom", "Archived from")}: {selectedTask.archived_from || "-"}</div>
            <div>{tr("context.checklistItems", "Checklist items")}: {Array.isArray(selectedTask.checklist) ? selectedTask.checklist.length : 0}</div>
          </div>
        ) : null}

        <div className="flex items-center gap-2">
          {!isCreate && selectedTask ? (
            <button
              type="button"
              onClick={onDeleteSelectedTask}
              disabled={syncBusy || !selectedTaskDeleteInfo.allowed}
              className={ui.buttonDangerClass}
              title={selectedTaskDeleteHint || undefined}
            >
              {tr("context.deleteTask", "Delete")}
            </button>
          ) : null}
          <button type="button" onClick={onSaveTask} disabled={syncBusy} className={ui.buttonPrimaryClass}>{syncBusy ? tr("context.saving", "Saving…") : (isCreate ? tr("context.createTask", "Create task") : tr("context.saveTask", "Save task"))}</button>
        </div>
        {!isCreate && selectedTaskDeleteHint ? (
          <div className={classNames("text-xs", ui.mutedTextClass)}>{selectedTaskDeleteHint}</div>
        ) : null}
      </div>
    </section>
  );
}

// ChatComposer renders the chat message composer.
import type { Dispatch, RefObject, SetStateAction } from "react";
import { useMemo } from "react";
import { Actor, GroupMeta, ReplyTarget } from "../../types";
import { classNames } from "../../utils/classNames";

export interface ChatComposerProps {
  isDark: boolean;
  isSmallScreen: boolean;
  selectedGroupId: string;
  actors: Actor[];
  recipientActors: Actor[];
  recipientActorsBusy?: boolean;
  groups: GroupMeta[];
  destGroupId: string;
  setDestGroupId: (groupId: string) => void;
  destGroupScopeLabel?: string;
  busy: string;

  // Reply
  replyTarget: ReplyTarget;
  onCancelReply: () => void;

  // Recipients
  toTokens: string[];
  onToggleRecipient: (token: string) => void;
  onClearRecipients: () => void;

  // Files
  composerFiles: File[];
  onRemoveComposerFile: (index: number) => void;
  appendComposerFiles: (files: File[]) => void;
  fileInputRef: RefObject<HTMLInputElement>;

  // Text input
  composerRef: RefObject<HTMLTextAreaElement>;
  composerText: string;
  setComposerText: Dispatch<SetStateAction<string>>;
  priority: "normal" | "attention";
  setPriority: (priority: "normal" | "attention") => void;
  onSendMessage: () => void;

  // Mention menu
  showMentionMenu: boolean;
  setShowMentionMenu: Dispatch<SetStateAction<boolean>>;
  mentionSuggestions: string[];
  mentionSelectedIndex: number;
  setMentionSelectedIndex: Dispatch<SetStateAction<number>>;
  setMentionFilter: Dispatch<SetStateAction<string>>;
  onAppendRecipientToken: (token: string) => void;
}

export function ChatComposer({
  isDark,
  isSmallScreen,
  selectedGroupId,
  actors,
  recipientActors,
  recipientActorsBusy,
  groups,
  destGroupId,
  setDestGroupId,
  destGroupScopeLabel,
  busy,
  replyTarget,
  onCancelReply,
  toTokens,
  onToggleRecipient,
  onClearRecipients,
  composerFiles,
  onRemoveComposerFile,
  appendComposerFiles,
  fileInputRef,
  composerRef,
  composerText,
  setComposerText,
  priority,
  setPriority,
  onSendMessage,
  showMentionMenu,
  setShowMentionMenu,
  mentionSuggestions,
  mentionSelectedIndex,
  setMentionSelectedIndex,
  setMentionFilter,
  onAppendRecipientToken,
}: ChatComposerProps) {
  const chipBaseClass =
    "flex-shrink-0 whitespace-nowrap text-[11px] px-2.5 py-1 rounded-full border transition-all";

  // Get display name for reply target
  const replyByDisplayName = useMemo(() => {
    if (!replyTarget?.by) return "";
    if (replyTarget.by === "user") return "user";
    const actor = actors.find(a => a.id === replyTarget.by);
    return actor?.title || replyTarget.by;
  }, [replyTarget, actors]);

  // Handle pasted files (clipboard items).
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const dt = e.clipboardData;
    if (!dt) return;

    const files: File[] = [];
    try {
      const items = Array.from(dt.items || []);
      for (const it of items) {
        if (!it || it.kind !== "file") continue;
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    } catch {
      // ignore
    }
    if (files.length === 0) {
      try {
        files.push(...Array.from(dt.files || []));
      } catch {
        // ignore
      }
    }

    if (files.length === 0) return;
    if (!selectedGroupId) return;

    // De-duplicate within a single paste.
    const seen = new Set<string>();
    const unique: File[] = [];
    for (const f of files) {
      const key = `${f.name}:${f.size}:${f.type}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(f);
    }
    if (unique.length === 0) return;

    e.preventDefault();
    appendComposerFiles(unique);
  };

  // Handle text changes.
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setComposerText(val);
    const target = e.target;
    // Use requestAnimationFrame to avoid forced reflow during layout.
    requestAnimationFrame(() => {
      target.style.height = "auto";
      target.style.height = Math.min(target.scrollHeight, 140) + "px";
    });

    // Detect @ mentions for the recipient helper menu.
    const lastAt = val.lastIndexOf("@");
    if (lastAt >= 0) {
      const afterAt = val.slice(lastAt + 1);
      if (
        (lastAt === 0 || val[lastAt - 1] === " " || val[lastAt - 1] === "\n") &&
        !afterAt.includes(" ") &&
        !afterAt.includes("\n")
      ) {
        setMentionFilter(afterAt);
        setShowMentionMenu(true);
        setMentionSelectedIndex(0);
      } else {
        setShowMentionMenu(false);
      }
    } else {
      setShowMentionMenu(false);
    }
  };

  // Handle keyboard shortcuts and mention navigation.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showMentionMenu && mentionSuggestions.length > 0) {
      const maxIndex = Math.min(mentionSuggestions.length, 8) - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionSelectedIndex((prev) => (prev >= maxIndex ? 0 : prev + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionSelectedIndex((prev) => (prev <= 0 ? maxIndex : prev - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        selectMention(mentionSuggestions[mentionSelectedIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowMentionMenu(false);
        setMentionSelectedIndex(0);
        return;
      }
    }
    if (e.key === "Enter" && !showMentionMenu) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        onSendMessage();
      }
    } else if (e.key === "Escape") {
      setShowMentionMenu(false);
      onCancelReply();
    }
  };

  // Select a mention from the menu.
  const selectMention = (selected: string | undefined) => {
    if (!selected) return;
    const lastAt = composerText.lastIndexOf("@");
    if (lastAt >= 0) {
      const before = composerText.slice(0, lastAt);
      setComposerText(before + selected + " ");
    }
    if (!toTokens.includes(selected)) {
      onAppendRecipientToken(selected);
    }
    setShowMentionMenu(false);
    setMentionSelectedIndex(0);
  };

  const canSend = composerText.trim() || composerFiles.length > 0;
  const isAttention = priority === "attention";
  const isCrossGroup = !!destGroupId && destGroupId !== selectedGroupId;
  const canChooseDestGroup =
    !!selectedGroupId && busy !== "send" && !replyTarget && composerFiles.length === 0;
  const destGroupDisabledReason = (() => {
    if (!selectedGroupId) return "Select a group first.";
    if (busy === "send") return "Busy.";
    if (replyTarget) return "Replies are always in the current group.";
    if (composerFiles.length > 0) return "Attachments cannot be sent cross-group yet.";
    return "";
  })();
  const fileDisabledReason = (() => {
    if (!selectedGroupId) return "Select a group first.";
    if (busy === "send") return "Busy.";
    if (isCrossGroup) return "Attachments cannot be sent cross-group yet.";
    return "Attach file";
  })();

  const groupOptions = useMemo(() => {
    const cur = String(selectedGroupId || "").trim();
    const list = (groups || []).filter((g) => String(g.group_id || "").trim());
    // Prefer showing the current group first.
    const sorted = list.slice().sort((a, b) => {
      const aId = String(a.group_id || "");
      const bId = String(b.group_id || "");
      if (aId === cur && bId !== cur) return -1;
      if (bId === cur && aId !== cur) return 1;
      const aTitle = String(a.title || "").trim().toLowerCase();
      const bTitle = String(b.title || "").trim().toLowerCase();
      if (aTitle && bTitle) return aTitle.localeCompare(bTitle);
      if (aTitle && !bTitle) return -1;
      if (!aTitle && bTitle) return 1;
      return aId.localeCompare(bId);
    });
    return sorted.map((g) => {
      const gid = String(g.group_id || "").trim();
      const title = String(g.title || "").trim();
      const topic = String(g.topic || "").trim();
      const label = title || topic || "Untitled group";
      return { gid, label };
    });
  }, [groups, selectedGroupId]);

  const groupSelectClass = useMemo(() => {
    if (!canChooseDestGroup || groupOptions.length === 0) {
      return isDark
        ? "bg-slate-900 text-slate-600 border-slate-800"
        : "bg-white text-gray-400 border-gray-200";
    }
    if (isCrossGroup) {
      return isDark
        ? "bg-blue-600/20 text-blue-100 border-blue-500/40 hover:border-blue-400/60"
        : "bg-blue-50 text-blue-700 border-blue-200 hover:border-blue-300";
    }
    return isDark
      ? "bg-cyan-500/10 text-slate-200 border-cyan-500/30 hover:border-cyan-400/40 hover:bg-cyan-500/15"
      : "bg-cyan-50 text-gray-800 border-cyan-200 hover:border-cyan-300";
  }, [canChooseDestGroup, groupOptions.length, isCrossGroup, isDark]);

  const groupCaretClass = useMemo(() => {
    if (!canChooseDestGroup || groupOptions.length === 0) return isDark ? "text-slate-600" : "text-gray-400";
    if (isCrossGroup) return isDark ? "text-blue-200" : "text-blue-700";
    return isDark ? "text-cyan-200" : "text-cyan-800";
  }, [canChooseDestGroup, groupOptions.length, isCrossGroup, isDark]);

  return (
    <footer
      className={classNames(
        "flex-shrink-0 border-t px-4 py-3 safe-area-inset-bottom",
        isDark ? "border-slate-800 bg-slate-950/80 backdrop-blur" : "border-gray-200 bg-white/80 backdrop-blur"
      )}
    >
      {/* Reply indicator */}
      {replyTarget && (
        <div className={classNames(
          "mb-2 flex items-center gap-2 text-xs rounded-xl px-3 py-2",
          isDark ? "text-slate-400 bg-slate-900/50" : "text-gray-500 bg-gray-100"
        )}>
          <span className={isDark ? "text-slate-500" : "text-gray-400"}>Replying to</span>
          <span className={classNames("font-medium", isDark ? "text-slate-300" : "text-gray-700")}>{replyByDisplayName}</span>
          <span className={classNames("truncate flex-1", isDark ? "text-slate-500" : "text-gray-400")}>"{replyTarget.text}"</span>
          <button
            className={classNames(
              "p-1 rounded-full",
              isDark ? "hover:bg-slate-800 text-slate-400 hover:text-slate-200" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"
            )}
            onClick={onCancelReply}
            title="Cancel reply"
            aria-label="Cancel reply"
          >
            ×
          </button>
        </div>
      )}

      {/* Recipient Selector */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className={classNames("text-xs font-medium flex-shrink-0", isDark ? "text-slate-500" : "text-gray-400")}>To</div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="relative flex-shrink-0">
            <select
              value={destGroupId || selectedGroupId || ""}
              onChange={(e) => setDestGroupId(e.target.value)}
              style={{ colorScheme: isDark ? "dark" : "light" }}
              className={classNames(
                "appearance-none pr-7 truncate max-w-[200px] sm:max-w-[240px]",
                "cccc-group-select",
                chipBaseClass,
                groupSelectClass
              )}
              disabled={!canChooseDestGroup || groupOptions.length === 0}
              title={
                destGroupDisabledReason ||
                (destGroupScopeLabel ? `Destination group • ${destGroupScopeLabel}` : "Destination group")
              }
              aria-label="Destination group"
            >
              {groupOptions.map((g) => (
                <option key={g.gid} value={g.gid}>
                  {g.label}
                </option>
              ))}
            </select>
            <div
              className={classNames(
                "pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px]",
                groupCaretClass
              )}
              aria-hidden="true"
            >
              ▾
            </div>
          </div>
        </div>
        <div className={classNames("flex-1 min-w-0 overflow-x-auto scrollbar-hide sm:overflow-visible", recipientActorsBusy ? "opacity-60" : "")}>
          <div className="flex items-center gap-1.5 flex-nowrap sm:flex-wrap">
            {/* Special tokens */}
            {["@all", "@foreman", "@peers"].map((tok) => {
              const active = toTokens.includes(tok);
              return (
                <button
                  key={tok}
                  className={classNames(
                    chipBaseClass,
                    active
                      ? "bg-emerald-600 text-white border-emerald-500 shadow-sm"
                      : isDark
                        ? "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-600 hover:text-slate-200"
                        : "bg-white text-gray-600 border-gray-200 hover:border-gray-300 hover:text-gray-800"
                  )}
                  onClick={() => onToggleRecipient(tok)}
                  disabled={!selectedGroupId || busy === "send"}
                  title={active ? "Remove recipient" : "Add recipient"}
                  aria-pressed={active}
                >
                  {tok}
                </button>
              );
            })}
            {/* Actor tokens - show title but use id as value */}
            {recipientActors.map((actor) => {
              const id = String(actor.id || "");
              if (!id) return null;
              const active = toTokens.includes(id);
              const displayName = actor.title || id;
              return (
                <button
                  key={id}
                  className={classNames(
                    chipBaseClass,
                    active
                      ? "bg-emerald-600 text-white border-emerald-500 shadow-sm"
                      : isDark
                        ? "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-600 hover:text-slate-200"
                        : "bg-white text-gray-600 border-gray-200 hover:border-gray-300 hover:text-gray-800"
                  )}
                  onClick={() => onToggleRecipient(id)}
                  disabled={!selectedGroupId || busy === "send" || !!recipientActorsBusy}
                  title={active ? `Remove ${displayName}` : `Add ${displayName}`}
                  aria-pressed={active}
                >
                  {displayName}
                </button>
              );
            })}
          </div>
        </div>
        {toTokens.length > 0 && (
          <button
            className={classNames(
              "flex-shrink-0 text-[10px] px-2 py-1 rounded-full transition-colors",
              isDark ? "text-slate-500 hover:text-slate-300 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            )}
            onClick={onClearRecipients}
            disabled={busy === "send"}
            title="Clear recipients"
          >
            clear
          </button>
        )}
      </div>

      {/* File list */}
      {composerFiles.length > 0 && (
        <div className={classNames("mb-3 flex flex-wrap gap-2", isDark ? "text-slate-300" : "text-gray-700")}>
          {composerFiles.map((f, idx) => (
            <span
              key={`${f.name}:${idx}`}
              className={classNames(
                "inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs max-w-full shadow-sm",
                isDark ? "border-slate-700 bg-slate-900" : "border-gray-200 bg-white"
              )}
              title={f.name}
            >
              <span className="truncate">{f.name}</span>
              <button
                className={classNames(
                  "flex-shrink-0 p-0.5 rounded-full",
                  isDark ? "text-slate-400 hover:text-white hover:bg-slate-700" : "text-slate-400 hover:text-gray-700 hover:bg-gray-100"
                )}
                onClick={() => onRemoveComposerFile(idx)}
                title="Remove file"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="flex gap-2 relative items-end">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            if (files.length > 0) appendComposerFiles(files);
            e.target.value = "";
          }}
        />
        <button
          className={classNames(
            "rounded-full p-2.5 text-lg transition-colors flex-shrink-0 shadow-sm border",
            isDark
              ? "bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200 hover:bg-slate-700 hover:border-slate-600"
              : "bg-white border-gray-200 text-gray-400 hover:text-gray-600 hover:bg-gray-50 hover:border-gray-300"
          )}
          onClick={() => fileInputRef.current?.click()}
          disabled={!selectedGroupId || busy === "send" || isCrossGroup}
          title={fileDisabledReason}
        >
          📎
        </button>
        <textarea
          ref={composerRef}
          className={classNames(
            "w-full rounded-3xl border px-4 py-3 text-sm resize-none min-h-[48px] max-h-[140px] transition-all focus:ring-2 focus:ring-offset-1",
            isDark
              ? "bg-slate-900 border-slate-700 text-slate-200 placeholder-slate-500 focus:border-blue-500/50 focus:ring-blue-500/20 focus:ring-offset-slate-900"
              : "bg-white border-gray-200 text-gray-900 placeholder-gray-400 focus:border-blue-400 focus:ring-blue-100 focus:ring-offset-white"
          )}
          placeholder={isSmallScreen ? "Message…" : "Message… (@ to mention, Ctrl+Enter to send)"}
          rows={1}
          value={composerText}
          onPaste={handlePaste}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onBlur={() => setTimeout(() => setShowMentionMenu(false), 150)}
          aria-label="Message input"
        />

        {/* Mention menu */}
        {showMentionMenu && mentionSuggestions.length > 0 && (
          <div
            className={classNames(
              "absolute bottom-full left-0 mb-2 w-56 max-h-48 overflow-auto rounded-xl border shadow-xl z-20",
              isDark ? "border-slate-700 bg-slate-900" : "border-gray-200 bg-white"
            )}
            role="listbox"
            aria-label="Mention suggestions"
          >
            {mentionSuggestions.slice(0, 8).map((s, idx) => (
              <button
                key={s}
                className={classNames(
                  "w-full text-left px-4 py-2.5 text-sm transition-colors",
                  isDark ? "text-slate-200" : "text-gray-700",
                  idx === mentionSelectedIndex
                    ? isDark ? "bg-slate-800" : "bg-blue-50 text-blue-700"
                    : isDark ? "hover:bg-slate-800" : "hover:bg-gray-50"
                )}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectMention(s);
                  composerRef.current?.focus();
                }}
                onMouseEnter={() => setMentionSelectedIndex(idx)}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Importance toggle */}
        <label
          className={classNames(
            "flex items-center gap-1.5 px-2 py-1.5 rounded-lg select-none min-h-[48px] transition-colors",
            busy === "send" || !selectedGroupId
              ? isDark
                ? "text-slate-600"
                : "text-gray-400"
              : isDark
                ? "text-slate-300 hover:bg-slate-800/60"
                : "text-gray-700 hover:bg-gray-100"
          )}
          title="Important message: recipients must acknowledge it"
        >
          <input
            type="checkbox"
            className="h-4 w-4 accent-amber-500"
            checked={isAttention}
            onChange={(e) => setPriority(e.target.checked ? "attention" : "normal")}
            disabled={busy === "send" || !selectedGroupId}
          />
          <span className={classNames("text-xs font-semibold whitespace-nowrap hidden sm:inline", isAttention ? (isDark ? "text-amber-200" : "text-amber-700") : "")}>
            Important
          </span>
        </label>

        {/* Send button */}
        <button
          className={classNames(
            "rounded-full px-5 py-2.5 text-sm font-semibold disabled:opacity-50 min-h-[48px] shadow-sm transition-all flex items-center justify-center",
            busy === "send" || !canSend
              ? isDark ? "bg-slate-800 text-slate-500" : "bg-gray-100 text-gray-400"
              : "bg-blue-600 hover:bg-blue-500 text-white shadow-blue-500/20"
          )}
          onClick={onSendMessage}
          disabled={busy === "send" || !canSend}
          aria-label="Send message"
        >
          {busy === "send" ? <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : "Send"}
        </button>
      </div>
    </footer>
  );
}

import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from "react";
import { Actor, LedgerEvent, ReplyTarget } from "../types";
import { VirtualMessageList } from "../components/VirtualMessageList";
import { classNames } from "../utils/classNames";

export interface ChatTabProps {
  isDark: boolean;
  isSmallScreen: boolean;
  selectedGroupId: string;
  actors: Actor[];
  busy: string;

  // Setup checklist
  showSetupCard: boolean;
  needsScope: boolean;
  needsActors: boolean;
  needsStart: boolean;
  hasForeman: boolean;
  onAddAgent: () => void;
  onStartGroup: () => void;

  // Messages + scrolling
  chatMessages: LedgerEvent[];
  scrollRef: MutableRefObject<HTMLDivElement | null>;
  showScrollButton: boolean;
  chatUnreadCount: number;
  onScrollButtonClick: () => void;
  onScrollChange: (isAtBottom: boolean) => void;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;

  // Composer
  replyTarget: ReplyTarget;
  onCancelReply: () => void;
  toTokens: string[];
  onToggleRecipient: (token: string) => void;
  onClearRecipients: () => void;

  composerFiles: File[];
  onRemoveComposerFile: (index: number) => void;
  appendComposerFiles: (files: File[]) => void;

  fileInputRef: RefObject<HTMLInputElement>;
  composerRef: RefObject<HTMLTextAreaElement>;
  composerText: string;
  setComposerText: Dispatch<SetStateAction<string>>;
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

export function ChatTab({
  isDark,
  isSmallScreen,
  selectedGroupId,
  actors,
  busy,
  showSetupCard,
  needsScope,
  needsActors,
  needsStart,
  hasForeman,
  onAddAgent,
  onStartGroup,
  chatMessages,
  scrollRef,
  showScrollButton,
  chatUnreadCount,
  onScrollButtonClick,
  onScrollChange,
  onReply,
  onShowRecipients,
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
  onSendMessage,
  showMentionMenu,
  setShowMentionMenu,
  mentionSuggestions,
  mentionSelectedIndex,
  setMentionSelectedIndex,
  setMentionFilter,
  onAppendRecipientToken,
}: ChatTabProps) {
  return (
    <>
      {showSetupCard && chatMessages.length > 0 && (
        <div className="flex-shrink-0 px-4 pt-4">
          <div
            className={classNames(
              "rounded-2xl border p-4 sm:p-5",
              isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
            )}
            role="region"
            aria-label="Setup checklist"
          >
            <div className={classNames("text-sm font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>Next steps</div>
            <div className="mt-3 space-y-2">
              {needsScope && (
                <div className={classNames("rounded-xl border px-3 py-2", isDark ? "border-slate-700 bg-slate-900/60" : "border-gray-200 bg-white")}>
                  <div className={classNames("text-xs font-medium", isDark ? "text-slate-200" : "text-gray-800")}>Attach a project folder</div>
                  <div className={classNames("mt-1 flex items-center justify-between gap-2 text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>
                    <code className={classNames("truncate", isDark ? "text-slate-300" : "text-gray-700")}>cccc attach . --group {selectedGroupId}</code>
                    <button
                      type="button"
                      className={classNames(
                        "flex-shrink-0 rounded-lg px-2 py-1 text-[11px] font-medium border",
                        isDark ? "border-slate-700 text-slate-300 hover:bg-slate-800" : "border-gray-200 text-gray-700 hover:bg-gray-50"
                      )}
                      onClick={async () => {
                        const cmd = `cccc attach . --group ${selectedGroupId}`;
                        try {
                          await navigator.clipboard.writeText(cmd);
                        } catch {
                          window.prompt("Copy command:", cmd);
                        }
                      }}
                    >
                      Copy
                    </button>
                  </div>
                </div>
              )}

              {needsActors && (
                <div
                  className={classNames(
                    "flex items-center justify-between gap-3 rounded-xl border px-3 py-2",
                    isDark ? "border-slate-700 bg-slate-900/60" : "border-gray-200 bg-white"
                  )}
                >
                  <div className="min-w-0">
                    <div className={classNames("text-xs font-medium", isDark ? "text-slate-200" : "text-gray-800")}>Add an agent</div>
                    <div className={classNames("text-[11px] truncate", isDark ? "text-slate-500" : "text-gray-500")}>Add a foreman first, then peers.</div>
                  </div>
                  <button
                    type="button"
                    className={classNames("flex-shrink-0 rounded-xl px-3 py-1.5 text-[11px] font-semibold", "bg-blue-600 hover:bg-blue-500 text-white")}
                    onClick={onAddAgent}
                  >
                    Add Agent
                  </button>
                </div>
              )}

              {needsStart && (
                <div
                  className={classNames(
                    "flex items-center justify-between gap-3 rounded-xl border px-3 py-2",
                    isDark ? "border-slate-700 bg-slate-900/60" : "border-gray-200 bg-white"
                  )}
                >
                  <div className="min-w-0">
                    <div className={classNames("text-xs font-medium", isDark ? "text-slate-200" : "text-gray-800")}>Start the group</div>
                    <div className={classNames("text-[11px] truncate", isDark ? "text-slate-500" : "text-gray-500")}>Launch your agents and begin chatting.</div>
                  </div>
                  <button
                    type="button"
                    className={classNames(
                      "flex-shrink-0 rounded-xl px-3 py-1.5 text-[11px] font-semibold",
                      "bg-emerald-600 hover:bg-emerald-500 text-white",
                      busy === "group-start" ? "opacity-60" : ""
                    )}
                    onClick={onStartGroup}
                    disabled={busy === "group-start"}
                  >
                    {busy === "group-start" ? "Starting…" : "Start"}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {chatMessages.length === 0 && showSetupCard ? (
        <div
          ref={scrollRef}
          className="flex-1 min-h-0 overflow-auto px-4 py-4 relative"
          role="log"
          aria-label="Chat messages"
        >
          <div className="flex flex-col items-center justify-center h-full text-center pb-20">
            <div className={classNames("w-full max-w-md", isDark ? "text-slate-200" : "text-gray-800")}>
              <div className={classNames("text-4xl mb-4", isDark ? "" : "")}>🧭</div>
              <div className={classNames("text-sm font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>Next steps</div>
              <div className={classNames("mt-4 space-y-2", isDark ? "" : "")}>
                {needsScope && (
                  <div
                    className={classNames(
                      "rounded-2xl border p-4 text-left",
                      isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
                    )}
                  >
                    <div className="text-xs font-semibold">Attach a project folder</div>
                    <div className={classNames("mt-2 flex items-center justify-between gap-2 text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>
                      <code className={classNames("truncate", isDark ? "text-slate-300" : "text-gray-700")}>cccc attach . --group {selectedGroupId}</code>
                      <button
                        type="button"
                        className={classNames(
                          "flex-shrink-0 rounded-lg px-2 py-1 text-[11px] font-medium border",
                          isDark ? "border-slate-700 text-slate-300 hover:bg-slate-800" : "border-gray-200 text-gray-700 hover:bg-gray-50"
                        )}
                        onClick={async () => {
                          const cmd = `cccc attach . --group ${selectedGroupId}`;
                          try {
                            await navigator.clipboard.writeText(cmd);
                          } catch {
                            window.prompt("Copy command:", cmd);
                          }
                        }}
                      >
                        Copy
                      </button>
                    </div>
                  </div>
                )}

                {needsActors && (
                  <div
                    className={classNames(
                      "rounded-2xl border p-4 text-left",
                      isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
                    )}
                  >
                    <div className="text-xs font-semibold">Add an agent</div>
                    <div className={classNames("mt-1 text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>Add a foreman first, then peers.</div>
                    <button
                      type="button"
                      className="mt-3 w-full rounded-xl px-4 py-2 text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white"
                      onClick={onAddAgent}
                    >
                      Add Agent
                    </button>
                  </div>
                )}

                {needsStart && (
                  <div
                    className={classNames(
                      "rounded-2xl border p-4 text-left",
                      isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
                    )}
                  >
                    <div className="text-xs font-semibold">Start the group</div>
                    <div className={classNames("mt-1 text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>Launch your agents and begin chatting.</div>
                    <button
                      type="button"
                      className={classNames(
                        "mt-3 w-full rounded-xl px-4 py-2 text-sm font-semibold",
                        "bg-emerald-600 hover:bg-emerald-500 text-white",
                        busy === "group-start" ? "opacity-60" : ""
                      )}
                      onClick={onStartGroup}
                      disabled={busy === "group-start"}
                    >
                      {busy === "group-start" ? "Starting…" : "Start Group"}
                    </button>
                  </div>
                )}

                {!needsScope && !needsActors && !needsStart && (
                  <div className={classNames("text-xs mt-4", isDark ? "text-slate-400" : "text-gray-500")}>
                    Team is ready. Say hi to your agents.
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <VirtualMessageList
          messages={chatMessages}
          actors={actors}
          isDark={isDark}
          groupId={selectedGroupId || ""}
          scrollRef={scrollRef}
          onReply={onReply}
          onShowRecipients={onShowRecipients}
          showScrollButton={showScrollButton}
          onScrollButtonClick={onScrollButtonClick}
          chatUnreadCount={chatUnreadCount}
          onScrollChange={onScrollChange}
        />
      )}

      {/* Composer */}
      <footer
        className={`flex-shrink-0 border-t px-4 py-3 safe-area-inset-bottom ${
          isDark ? "border-slate-800 bg-slate-950/80 backdrop-blur" : "border-gray-200 bg-white/80 backdrop-blur"
        }`}
      >
        {replyTarget && (
          <div className={`mb-2 flex items-center gap-2 text-xs rounded-xl px-3 py-2 ${isDark ? "text-slate-400 bg-slate-900/50" : "text-gray-500 bg-gray-100"}`}>
            <span className={isDark ? "text-slate-500" : "text-gray-400"}>Replying to</span>
            <span className={`font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{replyTarget.by}</span>
            <span className={`truncate flex-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>"{replyTarget.text}"</span>
            <button
              className={`p-1 rounded-full ${isDark ? "hover:bg-slate-800 text-slate-400 hover:text-slate-200" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
              onClick={onCancelReply}
              title="Cancel reply"
              aria-label="Cancel reply"
            >
              ×
            </button>
          </div>
        )}

        {/* Recipient Selector */}
        <div className="mb-3 flex items-center gap-2">
          <div className={`text-xs font-medium flex-shrink-0 ${isDark ? "text-slate-500" : "text-gray-400"}`}>To</div>
          <div className="flex-1 min-w-0 overflow-x-auto scrollbar-hide sm:overflow-visible">
            <div className="flex items-center gap-1.5 flex-nowrap sm:flex-wrap">
              {["@all", "@foreman", "@peers", ...actors.map((a) => String(a.id || ""))].map((tok) => {
                const t = tok.trim();
                if (!t) return null;
                const active = toTokens.includes(t);
                return (
                  <button
                    key={t}
                    className={classNames(
                      "flex-shrink-0 whitespace-nowrap text-[11px] px-2.5 py-1 rounded-full border transition-all",
                      active
                        ? "bg-emerald-600 text-white border-emerald-500 shadow-sm"
                        : isDark
                          ? "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-600 hover:text-slate-200"
                          : "bg-white text-gray-600 border-gray-200 hover:border-gray-300 hover:text-gray-800"
                    )}
                    onClick={() => onToggleRecipient(t)}
                    disabled={!selectedGroupId || busy === "send"}
                    title={active ? "Remove recipient" : "Add recipient"}
                    aria-pressed={active}
                  >
                    {t}
                  </button>
                );
              })}
            </div>
          </div>
          {toTokens.length > 0 && (
            <button
              className={`flex-shrink-0 text-[10px] px-2 py-1 rounded-full transition-colors ${
                isDark ? "text-slate-500 hover:text-slate-300 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
              }`}
              onClick={onClearRecipients}
              disabled={busy === "send"}
              title="Clear recipients"
            >
              clear
            </button>
          )}
        </div>

        {composerFiles.length > 0 && (
          <div className={`mb-3 flex flex-wrap gap-2 ${isDark ? "text-slate-300" : "text-gray-700"}`}>
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
            disabled={!selectedGroupId || busy === "send"}
            title="Attach file"
          >
            📎
          </button>
          <textarea
            ref={composerRef}
            className={`w-full rounded-3xl border px-4 py-3 text-sm resize-none min-h-[48px] max-h-[140px] transition-all focus:ring-2 focus:ring-offset-1 ${
              isDark
                ? "bg-slate-900 border-slate-700 text-slate-200 placeholder-slate-500 focus:border-blue-500/50 focus:ring-blue-500/20 focus:ring-offset-slate-900"
                : "bg-white border-gray-200 text-gray-900 placeholder-gray-400 focus:border-blue-400 focus:ring-blue-100 focus:ring-offset-white"
            }`}
            placeholder={isSmallScreen ? "Message…" : "Message… (@ to mention, Ctrl+Enter to send)"}
            rows={1}
            value={composerText}
            onPaste={(e) => {
              const dt = e.clipboardData;
              if (!dt) return;

              // Prefer `items` (more consistent across browsers). Fall back to `files`.
              // Do not read both, otherwise some browsers duplicate the same file.
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

              // Some clipboards provide duplicate file items for the same paste.
              const seen = new Set<string>();
              const unique: File[] = [];
              for (const f of files) {
                const key = `${f.name}:${f.size}:${f.type}`;
                if (seen.has(key)) continue;
                seen.add(key);
                unique.push(f);
              }
              if (unique.length === 0) return;

              // When files are present, treat paste as "attach" (avoid inserting stray text).
              e.preventDefault();
              appendComposerFiles(unique);
            }}
            onChange={(e) => {
              const val = e.target.value;
              setComposerText(val);
              const target = e.target;
              target.style.height = "auto";
              target.style.height = Math.min(target.scrollHeight, 140) + "px";
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
            }}
            onKeyDown={(e) => {
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
                  const selected = mentionSuggestions[mentionSelectedIndex];
                  if (selected) {
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
                  }
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
            }}
            onBlur={() => setTimeout(() => setShowMentionMenu(false), 150)}
            aria-label="Message input"
          />
          {showMentionMenu && mentionSuggestions.length > 0 && (
            <div
              className={`absolute bottom-full left-0 mb-2 w-56 max-h-48 overflow-auto rounded-xl border shadow-xl z-20 ${
                isDark ? "border-slate-700 bg-slate-900" : "border-gray-200 bg-white"
              }`}
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
                      ? isDark
                        ? "bg-slate-800"
                        : "bg-blue-50 text-blue-700"
                      : isDark
                        ? "hover:bg-slate-800"
                        : "hover:bg-gray-50"
                  )}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    const lastAt = composerText.lastIndexOf("@");
                    if (lastAt >= 0) {
                      const before = composerText.slice(0, lastAt);
                      setComposerText(before + s + " ");
                    }
                    if (!toTokens.includes(s)) {
                      onAppendRecipientToken(s);
                    }
                    setShowMentionMenu(false);
                    setMentionSelectedIndex(0);
                    composerRef.current?.focus();
                  }}
                  onMouseEnter={() => setMentionSelectedIndex(idx)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <button
            className={`rounded-full px-5 py-2.5 text-sm font-semibold disabled:opacity-50 min-h-[48px] shadow-sm transition-all flex items-center justify-center ${
              busy === "send" || (!composerText.trim() && composerFiles.length === 0)
                ? isDark
                  ? "bg-slate-800 text-slate-500"
                  : "bg-gray-100 text-gray-400"
                : "bg-blue-600 hover:bg-blue-500 text-white shadow-blue-500/20"
            }`}
            onClick={onSendMessage}
            disabled={busy === "send" || (!composerText.trim() && composerFiles.length === 0)}
            aria-label="Send message"
          >
            {busy === "send" ? <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : "Send"}
          </button>
        </div>
      </footer>
    </>
  );
}

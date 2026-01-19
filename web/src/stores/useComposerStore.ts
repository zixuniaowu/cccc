// Chat composer state store with per-group draft preservation.
import { create } from "zustand";
import type { ReplyTarget } from "../types";

interface GroupDraft {
  composerText: string;
  composerFiles: File[];
  toText: string;
  replyTarget: ReplyTarget;
  priority: "normal" | "attention";
  destGroupId: string;
}

interface ComposerState {
  // Current active state
  composerText: string;
  composerFiles: File[];
  toText: string;
  replyTarget: ReplyTarget;
  priority: "normal" | "attention";
  destGroupId: string;

  // Drafts per group (memory only)
  drafts: Record<string, GroupDraft>;

  // Actions
  setComposerText: (text: string | ((prev: string) => string)) => void;
  setComposerFiles: (files: File[]) => void;
  appendComposerFiles: (files: File[]) => void;
  setToText: (text: string) => void;
  setReplyTarget: (target: ReplyTarget) => void;
  setPriority: (priority: "normal" | "attention") => void;
  setDestGroupId: (groupId: string) => void;
  clearComposer: () => void;

  // Group switching: save current draft and load new group's draft
  switchGroup: (fromGroupId: string | null, toGroupId: string | null) => void;
  // Clear draft for a specific group
  clearDraft: (groupId: string) => void;
}

export const useComposerStore = create<ComposerState>((set, get) => ({
  composerText: "",
  composerFiles: [],
  toText: "",
  replyTarget: null,
  priority: "normal",
  destGroupId: "",
  drafts: {},

  setComposerText: (text) =>
    set((state) => ({
      composerText: typeof text === "function" ? text(state.composerText) : text,
    })),
  setComposerFiles: (files) => set({ composerFiles: files }),

  appendComposerFiles: (files) =>
    set((state) => {
      const keyOf = (f: File) => `${f.name}:${f.size}:${f.lastModified}`;
      const seen = new Set(state.composerFiles.map(keyOf));
      const next = state.composerFiles.slice();
      for (const f of files) {
        const k = keyOf(f);
        if (!seen.has(k)) {
          seen.add(k);
          next.push(f);
        }
      }
      return { composerFiles: next };
    }),

  setToText: (text) => set({ toText: text }),
  setReplyTarget: (target) => set({ replyTarget: target }),
  setPriority: (priority) => set({ priority }),
  setDestGroupId: (groupId) => set({ destGroupId: String(groupId || "").trim() }),

  clearComposer: () =>
    set({
      composerText: "",
      composerFiles: [],
      toText: "",
      replyTarget: null,
      priority: "normal",
    }),

  switchGroup: (fromGroupId, toGroupId) => {
    const state = get();
    const newDrafts = { ...state.drafts };

    // Save current state as draft for the old group (if any content)
    if (fromGroupId) {
      const hasContent =
        state.composerText.trim() ||
        state.composerFiles.length > 0 ||
        state.toText.trim() ||
        state.replyTarget;

      if (hasContent) {
        newDrafts[fromGroupId] = {
          composerText: state.composerText,
          composerFiles: state.composerFiles,
          toText: state.toText,
          replyTarget: state.replyTarget,
          priority: state.priority,
          destGroupId: state.destGroupId,
        };
      } else {
        delete newDrafts[fromGroupId];
      }
    }

    // Load draft for the new group
    const draft = toGroupId ? newDrafts[toGroupId] : null;

    set({
      drafts: newDrafts,
      composerText: draft?.composerText || "",
      composerFiles: draft?.composerFiles || [],
      toText: draft?.toText || "",
      replyTarget: draft?.replyTarget || null,
      priority: draft?.priority || "normal",
      destGroupId: draft?.destGroupId || (toGroupId || ""),
    });
  },

  clearDraft: (groupId) => {
    const state = get();
    const newDrafts = { ...state.drafts };
    delete newDrafts[groupId];
    set({ drafts: newDrafts });
  },
}));

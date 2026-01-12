// UI state store (tabs, sidebar, toasts, etc.).
import { create } from "zustand";

interface UINotice {
  message: string;
  actionLabel?: string;
  actionId?: string;
}

interface UIState {
  // State
  activeTab: string;
  busy: string;
  errorMsg: string;
  notice: UINotice | null;
  isTransitioning: boolean;
  sidebarOpen: boolean;
  showScrollButton: boolean;
  chatUnreadCount: number;
  isSmallScreen: boolean;
  chatFilter: "all" | "to_user" | "attention";

  // Actions
  setActiveTab: (tab: string) => void;
  setBusy: (busy: string) => void;
  setError: (msg: string) => void;
  showError: (msg: string) => void;
  dismissError: () => void;
  showNotice: (notice: UINotice) => void;
  dismissNotice: () => void;
  setTransitioning: (v: boolean) => void;
  setSidebarOpen: (v: boolean) => void;
  setShowScrollButton: (v: boolean) => void;
  setChatUnreadCount: (v: number) => void;
  incrementChatUnread: () => void;
  setSmallScreen: (v: boolean) => void;
  setChatFilter: (v: "all" | "to_user" | "attention") => void;
}

let errorTimeoutId: number | null = null;

export const useUIStore = create<UIState>((set) => ({
  // Initial state
  activeTab: "chat",
  busy: "",
  errorMsg: "",
  notice: null,
  isTransitioning: false,
  sidebarOpen: true,
  showScrollButton: false,
  chatUnreadCount: 0,
  isSmallScreen: false,
  chatFilter: "all",

  // Actions
  setActiveTab: (tab) => set({ activeTab: tab }),
  setBusy: (busy) => set({ busy }),
  setError: (msg) => set({ errorMsg: msg }),

  showError: (msg) => {
    if (errorTimeoutId) window.clearTimeout(errorTimeoutId);
    set({ errorMsg: msg });
    errorTimeoutId = window.setTimeout(() => {
      set({ errorMsg: "" });
      errorTimeoutId = null;
    }, 8000);
  },

  dismissError: () => {
    if (errorTimeoutId) {
      window.clearTimeout(errorTimeoutId);
      errorTimeoutId = null;
    }
    set({ errorMsg: "" });
  },

  showNotice: (notice) => set({ notice }),
  dismissNotice: () => set({ notice: null }),

  setTransitioning: (v) => set({ isTransitioning: v }),
  setSidebarOpen: (v) => set({ sidebarOpen: v }),
  setShowScrollButton: (v) => set({ showScrollButton: v }),
  setChatUnreadCount: (v) => set({ chatUnreadCount: v }),
  incrementChatUnread: () => set((state) => ({ chatUnreadCount: state.chatUnreadCount + 1 })),
  setSmallScreen: (v) => set({ isSmallScreen: v }),
  setChatFilter: (v) => set({ chatFilter: v }),
}));

import { registerSW } from "virtual:pwa-register";

type Notice = { message: string; actionLabel?: string; actionId?: string };

type NoticeFns = {
  showNotice: (notice: Notice) => void;
  dismissNotice: () => void;
};

let _updateSW: (() => Promise<void>) | null = null;

const OFFLINE_READY_SEEN_KEY = "cccc:pwa:offline-ready-seen";

export function initPWA(noticeFns: NoticeFns) {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;

  const updateSW = registerSW({
    onNeedRefresh() {
      noticeFns.showNotice({
        message: "Update available",
        actionLabel: "Reload",
        actionId: "pwa:update",
      });
    },
    onOfflineReady() {
      const hasSeen = (() => {
        try {
          return window.localStorage.getItem(OFFLINE_READY_SEEN_KEY) === "1";
        } catch {
          return false;
        }
      })();

      if (hasSeen) return;

      try {
        window.localStorage.setItem(OFFLINE_READY_SEEN_KEY, "1");
      } catch {
        // Ignore storage failures (private mode / embedded contexts).
      }

      noticeFns.showNotice({
        message: "Offline ready",
        actionLabel: "OK",
        actionId: "pwa:offline",
      });

      window.setTimeout(() => {
        noticeFns.dismissNotice();
      }, 3500);
    },
    onRegisterError() {
      // Do not surface as error toast: registration can fail in some embedded contexts.
    },
  });

  _updateSW = async () => {
    noticeFns.dismissNotice();
    await updateSW();
  };
}

export async function handlePwaNoticeAction(actionId: string, noticeFns: NoticeFns) {
  if (actionId === "pwa:update") {
    if (_updateSW) {
      await _updateSW();
    }
    return;
  }

  if (actionId === "pwa:offline") {
    noticeFns.dismissNotice();
  }
}

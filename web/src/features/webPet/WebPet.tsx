import { useCallback, useEffect, useRef, useState } from "react";
import { useGroupStore, useUIStore } from "../../stores";
import { getWebPetPosition, useWebPetStore } from "../../stores/useWebPetStore";
import {
  fetchActors,
  fetchContext,
  fetchGroup,
  fetchLedgerTail,
  fetchSettings,
  replyMessage,
  restartActor,
  sendMessage,
} from "../../services/api";
import { PetReminderBubble } from "./PetReminderBubble";
import { PetPanel } from "./PetPanel";
import { WebPetBubble } from "./WebPetBubble";
import { useWebPetData } from "./useWebPetData";
import { usePetPeerContext } from "./petPeerContext";
import { buildTaskProposalMessage } from "./taskProposal";
import { WEB_PET_BUBBLE_SIZE, WEB_PET_VIEWPORT_MARGIN } from "./constants";
import type { ReminderAction } from "./types";
import type { Actor, GroupContext, GroupDoc, GroupSettings, LedgerEvent } from "../../types";
import i18n from "../../i18n";

const lastKnownDesktopPetEnabledByGroup: Record<string, boolean> = {};
const BACKGROUND_REFRESH_MS = 30_000;
const EMPTY_EVENTS: LedgerEvent[] = [];

type RemotePetGroupState = {
  groupDoc: GroupDoc | null;
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  events: LedgerEvent[];
};

function tPet(key: string, fallback: string, vars?: Record<string, unknown>): string {
  return String(i18n.t(`webPet:${key}`, { defaultValue: fallback, ...(vars || {}) }));
}

function handleReminderAction(action: ReminderAction) {
  switch (action.type) {
    case "send_suggestion": {
      const text = String(action.text || "").trim();
      if (!text) return;
      const clientId = `pet_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const request = action.replyTo
        ? replyMessage(
            action.groupId,
            text,
            Array.isArray(action.to) ? action.to : [],
            action.replyTo,
            undefined,
            "normal",
            false,
            clientId,
          )
        : sendMessage(
            action.groupId,
            text,
            Array.isArray(action.to) ? action.to : [],
            undefined,
            "normal",
            false,
            clientId,
          );
      void request.then((resp) => {
        if (!resp.ok) {
          useUIStore.getState().showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        useUIStore.getState().showNotice({
          message: tPet("notice.suggestionSent", "Suggestion sent"),
        });
      }).catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : tPet("notice.suggestionSendFailed", "Failed to send suggestion");
        useUIStore.getState().showError(message);
      });
      break;
    }
    case "restart_actor": {
      void restartActor(action.groupId, action.actorId).then((resp) => {
        if (!resp.ok) {
          useUIStore.getState().showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        void useGroupStore.getState().refreshActors(action.groupId, { includeUnread: false });
        void useGroupStore.getState().refreshGroups();
        useUIStore.getState().showNotice({
          message: tPet("notice.restartRequested", "Restart requested"),
        });
      }).catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : tPet("notice.actorRestartFailed", "Failed to restart actor");
        useUIStore.getState().showError(message);
      });
      break;
    }
    case "task_proposal": {
      const text = buildTaskProposalMessage(action);
      if (!text.trim()) return;
      const clientId = `pet_task_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      void sendMessage(
        action.groupId,
        text,
        ["@foreman"],
        undefined,
        "normal",
        false,
        clientId,
      ).then((resp) => {
        if (!resp.ok) {
          useUIStore.getState().showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        useUIStore.getState().showNotice({
          message: tPet("notice.taskProposalSent", "Task proposal sent to foreman"),
        });
      }).catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : tPet("notice.taskProposalSendFailed", "Failed to send task proposal");
        useUIStore.getState().showError(message);
      });
      break;
    }
  }
}

function buildEmptyRemoteState(): RemotePetGroupState {
  return {
    groupDoc: null,
    actors: [],
    groupContext: null,
    groupSettings: null,
    events: [],
  };
}

export function WebPet({
  groupId,
  stackIndex = 0,
}: {
  groupId: string;
  stackIndex?: number;
}) {
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const selectedGroupDoc = useGroupStore((state) => state.groupDoc);
  const selectedGroupSettings = useGroupStore((state) => state.groupSettings);
  const selectedGroupContext = useGroupStore((state) => state.groupContext);
  const selectedEvents = useGroupStore((state) =>
    state.selectedGroupId === groupId ? state.chatByGroup[groupId]?.events || state.events : EMPTY_EVENTS,
  );
  const panelOpen = useWebPetStore((state) => state.panelOpenGroupId === groupId);
  const positions = useWebPetStore((state) => state.positions);
  const togglePanel = useWebPetStore((state) => state.togglePanel);
  const position = getWebPetPosition(groupId, positions, stackIndex);
  const [remoteState, setRemoteState] = useState<RemotePetGroupState>(() => buildEmptyRemoteState());
  const remoteRefreshEpochRef = useRef(0);

  const isSelectedGroup = String(selectedGroupId || "").trim() === String(groupId || "").trim();
  const groupDoc = isSelectedGroup ? selectedGroupDoc : remoteState.groupDoc;
  const groupSettings = isSelectedGroup ? selectedGroupSettings : remoteState.groupSettings;
  const groupContext = isSelectedGroup ? selectedGroupContext : remoteState.groupContext;
  const events = isSelectedGroup ? selectedEvents : remoteState.events;
  const petContext = usePetPeerContext({ groupId });
  const { catState, panelData, hint, reminders, activeReminder, dismissReminder, reaction } =
    useWebPetData({
      groupId,
      groupDoc,
      groupContext,
      events,
      petContext,
    });

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || !groupSettings) return;
    lastKnownDesktopPetEnabledByGroup[gid] = Boolean(groupSettings.desktop_pet_enabled);
  }, [groupId, groupSettings]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || isSelectedGroup || groupSettings !== null) return;
    const epoch = remoteRefreshEpochRef.current + 1;
    remoteRefreshEpochRef.current = epoch;
    let cancelled = false;
    void fetchSettings(gid).then((resp) => {
      if (cancelled || remoteRefreshEpochRef.current !== epoch || !resp.ok || !resp.result.settings) return;
      setRemoteState((state) => ({
        ...state,
        groupSettings: resp.result.settings,
      }));
    }).catch(() => {
      // Keep the pet on the last-known state when the background settings read fails.
    });
    return () => {
      cancelled = true;
    };
  }, [groupId, groupSettings, isSelectedGroup]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || isSelectedGroup) return;
    if (!groupSettings?.desktop_pet_enabled) return;

    let cancelled = false;

    const refresh = async () => {
      const epoch = remoteRefreshEpochRef.current + 1;
      remoteRefreshEpochRef.current = epoch;
      const [groupResp, actorsResp, contextResp, ledgerResp, settingsResp] =
        await Promise.all([
          fetchGroup(gid),
          fetchActors(gid, false),
          fetchContext(gid, { detail: "summary" }),
          fetchLedgerTail(gid),
          fetchSettings(gid),
        ]);
      if (cancelled || remoteRefreshEpochRef.current !== epoch) return;
      setRemoteState({
        groupDoc: groupResp.ok ? groupResp.result.group : null,
        actors: actorsResp.ok ? actorsResp.result.actors || [] : [],
        groupContext: contextResp.ok ? contextResp.result : null,
        groupSettings: settingsResp.ok ? settingsResp.result.settings || null : groupSettings,
        events: ledgerResp.ok ? ledgerResp.result.events || [] : [],
      });
    };

    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, BACKGROUND_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [groupId, groupSettings, isSelectedGroup]);

  const desktopPetEnabled = (() => {
    const gid = String(groupId || "").trim();
    if (!gid) return false;
    if (groupSettings) {
      return Boolean(groupSettings.desktop_pet_enabled);
    }
    return Boolean(lastKnownDesktopPetEnabledByGroup[gid]);
  })();

  const closePanel = useCallback(() => {
    if (panelOpen) togglePanel(groupId);
  }, [groupId, panelOpen, togglePanel]);

  if (!groupId || !desktopPetEnabled) {
    return null;
  }

  const panelAlign = (() => {
    if (typeof window === "undefined") {
      return "right" as const;
    }
    const estimatedPanelWidth = 320;
    const leftSpace = position.x - WEB_PET_VIEWPORT_MARGIN;
    const rightSpace =
      window.innerWidth -
      position.x -
      WEB_PET_BUBBLE_SIZE -
      WEB_PET_VIEWPORT_MARGIN;

    if (rightSpace >= estimatedPanelWidth || rightSpace >= leftSpace) {
      return "left" as const;
    }
    return "right" as const;
  })();

  return (
    <>
      {/* Backdrop: click outside panel/bubble to close */}
      {panelOpen ? (
        <div
          className="fixed inset-0 z-[1099]"
          onPointerDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
            closePanel();
          }}
          aria-hidden="true"
        />
      ) : null}
      <div
        className="pointer-events-none fixed z-[1100] overflow-visible"
        style={{
          left: position.x,
          top: position.y,
          width: WEB_PET_BUBBLE_SIZE,
          height: WEB_PET_BUBBLE_SIZE,
        }}
      >
        {panelOpen ? null : (
          <PetReminderBubble
            reminder={activeReminder}
            onDismiss={dismissReminder}
            onAction={handleReminderAction}
          />
        )}
        {panelOpen ? (
          <PetPanel
            panelData={panelData}
            reminders={reminders}
            align={panelAlign}
            onClose={closePanel}
            onAction={handleReminderAction}
            catSize={80}
            panelId={`web-pet-panel-${groupId}`}
          />
        ) : null}
        <WebPetBubble
          groupId={groupId}
          stackIndex={stackIndex}
          state={catState}
          hint={hint}
          reaction={reaction}
          panelOpen={panelOpen}
          onTogglePanel={() => togglePanel(groupId)}
        />
      </div>
    </>
  );
}

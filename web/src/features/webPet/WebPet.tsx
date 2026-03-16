import { useCallback } from "react";
import { useGroupStore, useModalStore, useUIStore } from "../../stores";
import { useWebPetStore } from "../../stores/useWebPetStore";
import { PetReminderBubble } from "./PetReminderBubble";
import { PetPanel } from "./PetPanel";
import { WebPetBubble } from "./WebPetBubble";
import { useWebPetData } from "./useWebPetData";
import type { ReminderAction } from "./types";

function handleReminderAction(action: ReminderAction) {
  switch (action.type) {
    case "open_chat":
      useUIStore.getState().setActiveTab("chat");
      useGroupStore.getState().setSelectedGroupId(action.groupId);
      void useGroupStore.getState().openChatWindow(action.groupId, action.eventId);
      break;
    case "open_task":
      useGroupStore.getState().setSelectedGroupId(action.groupId);
      useWebPetStore.getState().setPendingIntent({
        kind: "task",
        taskId: action.taskId,
      });
      useModalStore.getState().openModal("context");
      break;
    case "open_panel":
      useGroupStore.getState().setSelectedGroupId(action.groupId);
      if (!useWebPetStore.getState().panelOpen) {
        useWebPetStore.getState().togglePanel();
      }
      break;
  }
}

export function WebPet() {
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const desktopPetEnabled = useGroupStore((state) => Boolean(state.groupSettings?.desktop_pet_enabled));
  const panelOpen = useWebPetStore((state) => state.panelOpen);
  const position = useWebPetStore((state) => state.position);
  const togglePanel = useWebPetStore((state) => state.togglePanel);
  const { catState, panelData, hint, activeReminder, dismissReminder, reaction } =
    useWebPetData();

  const closePanel = useCallback(() => {
    if (panelOpen) togglePanel();
  }, [panelOpen, togglePanel]);

  if (!selectedGroupId || !desktopPetEnabled) {
    return null;
  }

  const panelAlign =
    typeof window !== "undefined" && position.x > window.innerWidth / 2
      ? "right"
      : "left";

  return (
    <>
      {/* Backdrop: click outside panel/bubble to close */}
      {panelOpen ? (
        <div
          className="fixed inset-0 z-[1099]"
          onPointerDown={closePanel}
          aria-hidden="true"
        />
      ) : null}
      <div
        className="pointer-events-none fixed z-[1100] overflow-visible"
        style={{
          left: position.x,
          top: position.y,
        }}
      >
        <PetReminderBubble
          reminder={activeReminder}
          onDismiss={dismissReminder}
          onAction={handleReminderAction}
        />
        {panelOpen ? (
          <PetPanel panelData={panelData} align={panelAlign} onClose={closePanel} />
        ) : null}
        <WebPetBubble state={catState} hint={hint} reaction={reaction} />
      </div>
    </>
  );
}

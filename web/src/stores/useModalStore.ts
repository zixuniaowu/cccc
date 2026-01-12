// Modal state store.
import { create } from "zustand";
import type { Actor } from "../types";

interface ModalState {
  // Modal visibility state
  modals: {
    context: boolean;
    settings: boolean;
    search: boolean;
    relay: boolean;
    addActor: boolean;
    createGroup: boolean;
    groupEdit: boolean;
    inbox: boolean;
    mobileMenu: boolean;
  };
  recipientsEventId: string | null;
  relayEventId: string | null;
  editingActor: Actor | null;

  // Actions
  openModal: (name: keyof ModalState["modals"]) => void;
  closeModal: (name: keyof ModalState["modals"]) => void;
  setRecipientsModal: (eventId: string | null) => void;
  setRelayModal: (eventId: string | null) => void;
  setEditingActor: (actor: Actor | null) => void;
}

export const useModalStore = create<ModalState>((set) => ({
  modals: {
    context: false,
    settings: false,
    search: false,
    relay: false,
    addActor: false,
    createGroup: false,
    groupEdit: false,
    inbox: false,
    mobileMenu: false,
  },
  recipientsEventId: null,
  relayEventId: null,
  editingActor: null,

  openModal: (name) =>
    set((state) => ({
      modals: { ...state.modals, [name]: true },
    })),

  closeModal: (name) =>
    set((state) => ({
      modals: { ...state.modals, [name]: false },
    })),

  setRecipientsModal: (eventId) => set({ recipientsEventId: eventId }),
  setRelayModal: (eventId) =>
    set((state) => ({
      relayEventId: eventId,
      modals: { ...state.modals, relay: !!eventId },
    })),
  setEditingActor: (actor) => set({ editingActor: actor }),
}));

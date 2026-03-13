// Tauri IPC bridge — listens for backend events and drives UI

import { setCatState } from "./cat.js";

/**
 * Initialize IPC listeners.
 * In development without Tauri, falls back to a demo mode cycling through states.
 */
async function initIPC() {
  if (window.__TAURI__) {
    const { listen } = window.__TAURI__.event;

    // Cat state change from Rust backend
    listen("cat-state-changed", (event) => {
      const { state } = event.payload;
      setCatState(state);
    });
  } else {
    // Demo mode: cycle through states for development
    console.log("[IPC] Tauri not detected, running in demo mode");
    startDemoMode();
  }
}

// --- Click handler for pet container ---

const petContainer = document.getElementById("pet-container");
const DRAG_THRESHOLD_PX = 6;

let activePointerId = null;
let pointerStartX = 0;
let pointerStartY = 0;
let pointerDragging = false;

petContainer.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  activePointerId = event.pointerId;
  pointerStartX = event.clientX;
  pointerStartY = event.clientY;
  pointerDragging = false;
  petContainer.classList.add("drag-armed");
});

petContainer.addEventListener("pointermove", (event) => {
  if (!window.__TAURI__ || activePointerId !== event.pointerId || pointerDragging) {
    return;
  }

  const deltaX = event.clientX - pointerStartX;
  const deltaY = event.clientY - pointerStartY;
  if (Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD_PX) {
    return;
  }

  pointerDragging = true;
  petContainer.classList.remove("drag-armed");

  const win = window.__TAURI__.window.getCurrentWindow();
  void win.startDragging().catch((err) => {
    console.warn("Failed to start dragging pet window:", err);
  });
});

petContainer.addEventListener("pointerup", (event) => {
  if (activePointerId !== event.pointerId) {
    return;
  }

  const shouldTogglePanel = !pointerDragging && event.button === 0 && event.detail <= 1;
  resetPointerInteraction();

  if (!shouldTogglePanel || !window.__TAURI__) {
    return;
  }

  const { invoke } = window.__TAURI__.core;
  void invoke("toggle_panel_window");
});

petContainer.addEventListener("pointercancel", () => {
  resetPointerInteraction();
});

petContainer.addEventListener("dblclick", (event) => {
  event.preventDefault();
  event.stopPropagation();
});

// --- Escape key to dismiss panel ---

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && window.__TAURI__) {
    const { invoke } = window.__TAURI__.core;
    void invoke("toggle_panel_window");
  }
});

function resetPointerInteraction() {
  activePointerId = null;
  pointerDragging = false;
  petContainer.classList.remove("drag-armed");
}

// --- Demo mode for development without Tauri ---

function startDemoMode() {
  const states = ["napping", "working", "busy", "needs_you"];
  let idx = 0;

  const conn = { connected: true, message: "connected" };
  const demoDetails = {
    napping: { teamName: "CCCC Dev", agents: [], actionItems: [], connection: conn },
    working: { teamName: "CCCC Dev", agents: [], actionItems: [], connection: conn },
    busy: { teamName: "CCCC Dev", agents: [], actionItems: [], connection: conn },
    needs_you: { teamName: "CCCC Dev", agents: [], actionItems: [], connection: conn },
  };

  setInterval(() => {
    const state = states[idx];
    setCatState(state);
    console.log("[IPC] Panel preview payload", demoDetails[state]);
    idx = (idx + 1) % states.length;
  }, 5000);

  // Set initial state
  setCatState("napping");
}

initIPC();

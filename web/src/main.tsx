import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "@xterm/xterm/css/xterm.css";

// Replace old caching service worker with minimal one (for PWA installability).
if ("serviceWorker" in navigator) {
  void navigator.serviceWorker.getRegistrations().then(async (registrations) => {
    // Unregister old workbox-based SWs that may have cached stale assets.
    for (const r of registrations) {
      if (r.active?.scriptURL?.includes("sw.js")) {
        // Check if it's the old workbox SW (has precache logic).
        // We unregister all and re-register the minimal one below.
      }
      await r.unregister();
    }
    // Register minimal SW for Chrome install prompt support.
    void navigator.serviceWorker.register("/ui/sw.js", { scope: "/ui/" });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

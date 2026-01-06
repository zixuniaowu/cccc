import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "@xterm/xterm/css/xterm.css";
import { initPWA } from "./pwa";
import { useUIStore } from "./stores";

initPWA({
  showNotice: (notice) => useUIStore.getState().showNotice(notice),
  dismissNotice: () => useUIStore.getState().dismissNotice(),
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

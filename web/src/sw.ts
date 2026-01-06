/// <reference lib="WebWorker" />
/// <reference types="vite-plugin-pwa/client" />

import { clientsClaim } from "workbox-core";
import { cleanupOutdatedCaches, precacheAndRoute, createHandlerBoundToURL } from "workbox-precaching";
import { registerRoute } from "workbox-routing";
import { NetworkOnly } from "workbox-strategies";

declare const self: ServiceWorkerGlobalScope;

clientsClaim();

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

registerRoute(({ url }) => url.origin === self.location.origin && url.pathname.startsWith("/api"), new NetworkOnly());

const navigationHandler = createHandlerBoundToURL("/ui/index.html");

registerRoute(
  ({ request, url }) => {
    if (request.mode !== "navigate") return false;
    if (url.origin !== self.location.origin) return false;
    if (!url.pathname.startsWith("/ui")) return false;
    if (url.pathname.startsWith("/ui/assets/")) return false;
    return true;
  },
  navigationHandler
);

self.addEventListener("message", (event) => {
  const data = (event as unknown as { data?: unknown }).data;
  const isSkipWaiting =
    !!data &&
    typeof data === "object" &&
    "type" in data &&
    (data as Record<string, unknown>).type === "SKIP_WAITING";
  if (isSkipWaiting) {
    void self.skipWaiting();
  }
});

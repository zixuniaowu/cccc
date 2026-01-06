import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      scope: "/ui/",
      base: "/ui/",
      injectRegister: false,
      registerType: "prompt",
      includeAssets: ["favicon.ico", "favicon.png", "logo.png", "logo.svg", "manifest.webmanifest"],
      manifest: false,
      injectManifest: {
        // We handle HTML navigation fallback in the custom SW.
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
    }),
  ],
  base: "/ui/",
  resolve: {
    // Prefer the CJS build for xterm to avoid a minification bug that can break
    // the ESM build's `requestMode` handler (seen as `ReferenceError: i is not defined`).
    alias: [
      { find: /^@xterm\/xterm$/, replacement: "@xterm/xterm/lib/xterm.js" },
    ],
  },
  build: {
    outDir: "../src/cccc/ports/web/dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/index.js",
        chunkFileNames: "assets/chunk-[name].js",
        assetFileNames: "assets/[name][extname]",
        // Split large deps into dedicated chunks to avoid oversized bundles.
        manualChunks: {
          // xterm (~400KB): keep separate to avoid bloating AgentTab.
          xterm: ["@xterm/xterm", "@xterm/addon-fit"],
          // React core
          "react-vendor": ["react", "react-dom"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8848",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});

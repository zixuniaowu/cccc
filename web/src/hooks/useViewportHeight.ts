// useViewportHeight: Handles the virtual keyboard on mobile.
//
// Problem: CSS `100dvh` adjusts for browser chrome but NOT the virtual keyboard.
// When the keyboard opens on mobile, the bottom of the page (including the composer)
// gets pushed behind the keyboard, making it invisible.
//
// Solution: Listen to `window.visualViewport` resize events and set a CSS custom
// property `--vh-offset` on the document root. The app shell uses this to shrink
// its height when the keyboard is open.
import { useEffect } from "react";

export function useViewportHeight() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return; // Not supported (desktop browsers, older mobile)

    function onResize() {
      if (!vv) return;
      // The offset is the difference between the layout viewport and the visual viewport.
      // When the keyboard opens, vv.height shrinks but window.innerHeight stays the same.
      const offset = window.innerHeight - vv.height;
      // Only apply if significant (> 100px means keyboard is likely open)
      if (offset > 100) {
        document.documentElement.style.setProperty("--vk-offset", `${offset}px`);
      } else {
        document.documentElement.style.setProperty("--vk-offset", "0px");
      }
    }

    vv.addEventListener("resize", onResize);
    // Initial call
    onResize();

    return () => {
      vv.removeEventListener("resize", onResize);
      document.documentElement.style.removeProperty("--vk-offset");
    };
  }, []);
}

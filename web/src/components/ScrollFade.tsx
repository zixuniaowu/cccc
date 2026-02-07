import { useRef, useEffect, useState, type ReactNode, type CSSProperties } from "react";
import { classNames } from "../utils/classNames";

interface ScrollFadeProps {
  children: ReactNode;
  className?: string;
  /** Extra classes for the inner scrollable div */
  innerClassName?: string;
  /** Fade width in px (default 24) */
  fadeWidth?: number;
  /** Direction: horizontal (default) or vertical */
  direction?: "horizontal" | "vertical";
  style?: CSSProperties;
}

/**
 * Wrapper that adds gradient fade masks on edges when content overflows.
 * Solves the "scrollbar-hide with no affordance" pattern used across the app.
 */
export function ScrollFade({
  children,
  className,
  innerClassName,
  fadeWidth = 24,
  direction = "horizontal",
  style,
}: ScrollFadeProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollStart, setCanScrollStart] = useState(false);
  const [canScrollEnd, setCanScrollEnd] = useState(false);

  const check = () => {
    const el = scrollRef.current;
    if (!el) return;
    if (direction === "horizontal") {
      const tol = 2;
      setCanScrollStart(el.scrollLeft > tol);
      setCanScrollEnd(el.scrollLeft + el.clientWidth < el.scrollWidth - tol);
    } else {
      const tol = 2;
      setCanScrollStart(el.scrollTop > tol);
      setCanScrollEnd(el.scrollTop + el.clientHeight < el.scrollHeight - tol);
    }
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    check();
    el.addEventListener("scroll", check, { passive: true });

    let ro: ResizeObserver | null = null;
    let mo: MutationObserver | null = null;
    const onWindowResize = () => check();

    const observeWithResizeObserver = () => {
      if (typeof ResizeObserver === "undefined") return;
      ro = new ResizeObserver(check);
      ro.observe(el);
      for (const child of Array.from(el.children)) {
        ro.observe(child);
      }
    };

    observeWithResizeObserver();
    if (!ro) {
      window.addEventListener("resize", onWindowResize);
    }

    if (typeof MutationObserver !== "undefined") {
      mo = new MutationObserver(() => {
        check();
        if (ro) {
          ro.disconnect();
          observeWithResizeObserver();
        }
      });
      mo.observe(el, { childList: true });
    }

    return () => {
      el.removeEventListener("scroll", check);
      ro?.disconnect();
      mo?.disconnect();
      window.removeEventListener("resize", onWindowResize);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction]);

  const isH = direction === "horizontal";
  const maskParts: string[] = [];
  if (canScrollStart) {
    maskParts.push(
      isH
        ? `linear-gradient(to right, transparent, black ${fadeWidth}px)`
        : `linear-gradient(to bottom, transparent, black ${fadeWidth}px)`
    );
  }
  if (canScrollEnd) {
    maskParts.push(
      isH
        ? `linear-gradient(to left, transparent, black ${fadeWidth}px)`
        : `linear-gradient(to top, transparent, black ${fadeWidth}px)`
    );
  }

  const maskStyle: CSSProperties =
    maskParts.length === 2
      ? {
          WebkitMaskImage: maskParts.join(", "),
          maskImage: maskParts.join(", "),
          WebkitMaskComposite: "destination-in",
          maskComposite: "intersect",
        }
      : maskParts.length === 1
        ? {
            WebkitMaskImage: maskParts[0],
            maskImage: maskParts[0],
          }
        : {};

  return (
    <div className={classNames("relative", className)} style={{ ...style, ...maskStyle }}>
      <div
        ref={scrollRef}
        className={classNames(
          isH ? "overflow-x-auto scrollbar-hide" : "overflow-y-auto scrollbar-hide",
          innerClassName
        )}
      >
        {children}
      </div>
    </div>
  );
}

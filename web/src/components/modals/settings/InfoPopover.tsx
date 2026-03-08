import { useCallback, useState } from "react";
import type { HTMLAttributes, ReactNode } from "react";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  useClick,
  useDismiss,
  useFloating,
  useInteractions,
  useRole,
} from "@floating-ui/react";

interface InfoPopoverProps {
  isDark: boolean;
  title: string;
  content: ReactNode;
  placement?: "top" | "top-start" | "top-end" | "bottom" | "bottom-start" | "bottom-end" | "right" | "left";
  maxWidthClassName?: string;
  children: (
    getReferenceProps: (userProps?: HTMLAttributes<HTMLElement>) => Record<string, unknown>,
    setReference: (node: HTMLElement | null) => void
  ) => ReactNode;
}

export function InfoPopover({
  isDark,
  title,
  content,
  placement = "bottom-end",
  maxWidthClassName = "max-w-[280px]",
  children,
}: InfoPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open: isOpen,
    onOpenChange: setIsOpen,
    placement,
    middleware: [offset(8), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
    strategy: "fixed",
  });

  const isPositioned = context.isPositioned;
  const click = useClick(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, { role: "dialog" });
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss, role]);

  const setReference = useCallback(
    (node: HTMLElement | null) => {
      refs.setReference(node);
    },
    [refs]
  );

  const setFloating = useCallback(
    (node: HTMLElement | null) => {
      refs.setFloating(node);
    },
    [refs]
  );

  return (
    <>
      {children(getReferenceProps, setReference)}
      <FloatingPortal>
        {isOpen && (
          <div
            ref={setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className={`z-max ${maxWidthClassName} rounded-xl border shadow-2xl px-3 py-3 text-[11px] leading-6 transition-opacity duration-150 ${
              isPositioned ? "opacity-100" : "opacity-0"
            } ${isDark ? "bg-slate-900 border-slate-700 text-slate-300" : "bg-white border-gray-200 text-gray-600"}`}
          >
            <div className="font-semibold mb-1 text-emerald-500">{title}</div>
            {content}
          </div>
        )}
      </FloatingPortal>
    </>
  );
}

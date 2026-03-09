import type { ReactNode, Ref } from "react";

interface ModalFrameProps {
  isDark: boolean;
  onClose: () => void;
  titleId: string;
  title: ReactNode;
  closeAriaLabel: string;
  panelClassName: string;
  modalRef?: Ref<HTMLDivElement>;
  children: ReactNode;
}

export function ModalFrame({
  isDark,
  onClose,
  titleId,
  title,
  closeAriaLabel,
  panelClassName,
  modalRef,
  children,
}: ModalFrameProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-stretch sm:items-center justify-center p-0 sm:p-4 animate-fade-in">
      <div
        className="absolute inset-0 glass-overlay"
        onPointerDown={onClose}
        aria-hidden="true"
      />

      <div
        className={`relative flex flex-col border shadow-2xl animate-scale-in rounded-none sm:rounded-xl glass-modal ${panelClassName}`}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between px-5 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)]"
        >
          <h2 id={titleId} className="text-lg font-semibold text-[var(--color-text-primary)]">
            {title}
          </h2>
          <button
            onClick={onClose}
            className="text-xl leading-none min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors glass-btn text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
            aria-label={closeAriaLabel}
          >
            ×
          </button>
        </div>

        {children}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
};

export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((confirmed: boolean) => void) | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogId = useId();

  const confirm = useCallback((next: ConfirmOptions) => new Promise<boolean>(resolve => {
    resolver.current?.(false);
    resolver.current = resolve;
    setOptions(next);
  }), []);

  const close = useCallback((confirmed: boolean) => {
    resolver.current?.(confirmed);
    resolver.current = null;
    setOptions(null);
  }, []);

  useEffect(() => {
    if (!options) return;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [options, close]);

  const dialog = options ? <div className="modal-backdrop confirm-dialog-backdrop" role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget) close(false); }}>
    <section className="confirm-dialog" role="dialog" aria-modal="true"
      aria-labelledby={`${dialogId}-title`} aria-describedby={`${dialogId}-message`}>
      <span className="confirm-dialog-icon" aria-hidden="true">!</span>
      <h2 id={`${dialogId}-title`}>{options.title}</h2>
      <p id={`${dialogId}-message`}>{options.message}</p>
      <div className="confirm-dialog-actions">
        <button ref={cancelRef} type="button" className="secondary-button" onClick={() => close(false)}>
          {options.cancelLabel || "Cancel"}
        </button>
        <button type="button" className="reject-button" onClick={() => close(true)}>
          {options.confirmLabel || "Continue"}
        </button>
      </div>
    </section>
  </div> : null;

  return { confirm, dialog };
}

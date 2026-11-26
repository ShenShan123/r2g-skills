import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { t } from "../lib/i18n";

/** Modal confirmation for a consequential action: focus trapped, Escape and the
 *  backdrop cancel, the safe choice is focused first. `tone="danger"` paints the
 *  confirm button red (deleting, stopping running work). */
export default function ConfirmDialog({ title, children, confirmLabel, tone = "danger", onConfirm, onCancel }: {
  title: string; children: React.ReactNode; confirmLabel: string; tone?: "danger" | "primary";
  onConfirm: () => void; onCancel: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const id = useId();
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById("root");
    const wasInert = root?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    cancel.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); cancelRef.current();
      } else if (event.key === "Tab") {
        const buttons = dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
        if (!buttons?.length) return;
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      if (root) root.inert = wasInert;
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      onClick={(event) => { event.stopPropagation(); if (event.target === event.currentTarget) onCancel(); }}>
      <div ref={dialog} role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
        className="w-full max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl border border-line bg-panel p-5 shadow-xl md:p-6">
        <h2 id={`${id}-title`} className="text-lg font-semibold">{title}</h2>
        <div id={`${id}-description`} className="mt-3 space-y-3 text-sm leading-6 text-muted">{children}</div>
        <div className="mt-6 flex justify-end gap-2">
          <button ref={cancel} type="button" className="btn-ghost" onClick={onCancel}>{t("取消")}</button>
          <button type="button" onClick={onConfirm}
            className={tone === "danger"
              ? "btn border border-bad/25 bg-bad/10 text-bad hover:bg-bad/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bad/50"
              : "btn-primary"}>{confirmLabel}</button>
        </div>
      </div>
    </div>, document.body);
}

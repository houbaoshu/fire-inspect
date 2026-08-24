import { useEffect, useId, useRef, type ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  tone?: "danger" | "warning" | "default";
  busy?: boolean;
  requireReason?: boolean;
  reason?: string;
  onReasonChange?: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认",
  tone = "default",
  busy = false,
  requireReason = false,
  reason = "",
  onReasonChange,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      className="confirm-dialog"
      ref={dialogRef}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <div className={`dialog-tone dialog-${tone}`}>
        <h2 id={titleId}>{title}</h2>
        <div className="dialog-description">{description}</div>
        {requireReason ? (
          <label className="field">
            <span>原因 <b aria-hidden="true">*</b></span>
            <textarea
              value={reason}
              onChange={(event) => onReasonChange?.(event.target.value)}
              placeholder="请填写可供审计追溯的原因"
              rows={3}
              maxLength={300}
              autoFocus
            />
          </label>
        ) : null}
        <div className="dialog-actions">
          <button className="button button-ghost" type="button" disabled={busy} onClick={onCancel}>取消</button>
          <button
            className={`button ${tone === "danger" ? "button-danger" : "button-primary"}`}
            type="button"
            disabled={busy || (requireReason && reason.trim().length < 2)}
            onClick={onConfirm}
          >
            {busy ? "正在处理…" : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}

import type { ReactNode } from "react";

export function LoadingState({ label = "正在加载", fullPage = false }: { label?: string; fullPage?: boolean }) {
  return (
    <div className={fullPage ? "loading-state loading-full-page" : "loading-state"} role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="notice notice-error" role="alert">
      <div>
        <strong>未能加载</strong>
        <p>{message}</p>
      </div>
      {onRetry ? <button className="button button-secondary" type="button" onClick={onRetry}>重试</button> : null}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-mark" aria-hidden="true">◇</span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

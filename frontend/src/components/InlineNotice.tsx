import type { PropsWithChildren } from "react";

export function InlineNotice({ tone = "info", children }: PropsWithChildren<{ tone?: "info" | "warning" | "error" | "success" }>) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

import type { JobStatus } from "../api/types";

type DisplayStatus = JobStatus | "draft" | "confirmed" | "candidate" | "withdrawn" | "not_started" | "idle";

const LABELS: Record<DisplayStatus, string> = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  superseded: "结果已过期",
  draft: "初稿",
  confirmed: "已确认",
  candidate: "候选",
  withdrawn: "已撤回",
  not_started: "未开始",
  idle: "未生成",
};

export function StatusPill({ status, label }: { status: DisplayStatus; label?: string }) {
  return <span className={`status-pill status-${status}`}>{label ?? LABELS[status]}</span>;
}

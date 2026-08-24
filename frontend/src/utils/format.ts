const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  hourCycle: "h23",
});

const shanghaiInputFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  hourCycle: "h23",
});

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormatter.format(date).replaceAll("/", "-");
}

export function formatDuration(milliseconds?: number | null): string {
  if (milliseconds === undefined || milliseconds === null || milliseconds < 0) return "—";
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":")
    : [minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === undefined || bytes === null || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}

export function toShanghaiDateTimeInput(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = Object.fromEntries(
    shanghaiInputFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function shanghaiDateTimeInputToIso(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) {
    throw new Error("INVALID_SHANGHAI_DATETIME");
  }
  const date = new Date(`${value.length === 16 ? `${value}:00` : value}+08:00`);
  if (Number.isNaN(date.getTime())) throw new Error("INVALID_SHANGHAI_DATETIME");
  return date.toISOString();
}

export function inspectionTypeLabel(value: string): string {
  return (
    {
      mass_event_precheck: "大型群众性活动举办前的检查",
      construction_site: "建设工程施工现场检查",
      complaint_verification: "对举报投诉的核查",
      reinspection: "复查",
      resume_operation: "申请恢复施工、使用、生产、经营的检查",
      lift_seal: "申请解除临时查封的检查",
      other: "其它检查",
    }[value] ?? value
  );
}

export const INSPECTION_TYPES = [
  ["mass_event_precheck", "大型群众性活动举办前的检查"],
  ["construction_site", "建设工程施工现场检查"],
  ["complaint_verification", "对举报投诉的核查"],
  ["reinspection", "复查"],
  ["resume_operation", "申请恢复施工、使用、生产、经营的检查"],
  ["lift_seal", "申请解除临时查封的检查"],
  ["other", "其它检查"],
] as const;

export function compactJson(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "—";
  }
}

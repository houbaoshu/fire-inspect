import type { JobTaskType, MaterialStepName } from "./types";

export const MATERIAL_STEP_ORDER = [
  "transcode",
  "audio_extract",
  "asr",
  "keyframes",
  "vision",
  "fact_extraction",
] as const satisfies readonly MaterialStepName[];

const MATERIAL_STEPS_BY_KIND: Record<string, readonly MaterialStepName[]> = {
  video: MATERIAL_STEP_ORDER,
  audio: ["asr", "fact_extraction"],
  image: ["vision"],
  text: ["fact_extraction"],
};

export const JOB_LABELS: Record<JobTaskType, string> = {
  transcode: "兼容转码",
  audio_extract: "音频提取",
  asr: "语音转写",
  keyframes: "关键画面",
  vision: "视觉描述",
  fact_extraction: "事实提取",
  knowledge_parse: "知识解析",
  embedding: "向量索引",
  template_recognition: "模板识别",
  document_generation: "文书生成",
  document_render: "文书渲染",
  deletion_cleanup: "删除清理",
};

export function materialStepNames(kind: string): readonly MaterialStepName[] {
  return MATERIAL_STEPS_BY_KIND[kind] ?? [];
}

export function jobLabel(taskType: string): string {
  return JOB_LABELS[taskType as JobTaskType] ?? taskType;
}

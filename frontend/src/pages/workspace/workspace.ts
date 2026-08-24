import type { Material, MaterialStep } from "../../api/types";

export interface WorkspacePanelProps {
  inspectionId: string;
}

export function materialSteps(material: Material): MaterialStep[] {
  if (!material.steps) return [];
  if (Array.isArray(material.steps)) return material.steps;
  return Object.entries(material.steps).map(([name, value]) => {
    if (typeof value === "string") return { name, status: value } as MaterialStep;
    return { ...value, name: value.name || name };
  });
}

export const STEP_LABELS: Record<string, string> = {
  probe: "媒体探测",
  transcode: "兼容播放",
  audio_extract: "音频提取",
  asr: "中文转写",
  keyframes: "关键画面",
  vision: "画面描述",
  facts: "候选事实",
  fact_extraction: "候选事实",
};

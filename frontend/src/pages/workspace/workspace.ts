import type { Material, MaterialStep } from "../../api/types";
export { JOB_LABELS as STEP_LABELS, materialStepNames } from "../../api/jobContract";

export interface WorkspacePanelProps {
  inspectionId: string;
}

export function materialSteps(material: Material): MaterialStep[] {
  return Array.isArray(material.steps) ? material.steps : [];
}

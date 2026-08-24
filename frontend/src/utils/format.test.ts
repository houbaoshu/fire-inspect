import { describe, expect, it } from "vitest";

import {
  INSPECTION_TYPES,
  inspectionTypeLabel,
  shanghaiDateTimeInputToIso,
  toShanghaiDateTimeInput,
} from "./format";

describe("Shanghai date-time formatting", () => {
  it("converts UTC values to the explicit Asia/Shanghai form and back", () => {
    expect(toShanghaiDateTimeInput("2026-08-24T00:30:00.000Z")).toBe("2026-08-24T08:30");
    expect(shanghaiDateTimeInputToIso("2026-08-24T08:30")).toBe("2026-08-24T00:30:00.000Z");
  });

  it("rejects browser-dependent or incomplete date-time input", () => {
    expect(() => shanghaiDateTimeInputToIso("2026-08-24")).toThrow("INVALID_SHANGHAI_DATETIME");
    expect(() => shanghaiDateTimeInputToIso("not-a-date")).toThrow("INVALID_SHANGHAI_DATETIME");
  });
});

describe("inspection types", () => {
  it("exposes all seven PRD choices including the backend lift_seal value", () => {
    expect(INSPECTION_TYPES).toHaveLength(7);
    expect(INSPECTION_TYPES.map(([value]) => value)).toContain("lift_seal");
    expect(INSPECTION_TYPES.map(([value]) => value)).not.toContain("lift_temporary_seal");
    expect(inspectionTypeLabel("lift_seal")).toBe("申请解除临时查封的检查");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill } from "./StatusPill";

describe("StatusPill", () => {
  it.each([
    ["queued", "排队中"],
    ["running", "处理中"],
    ["succeeded", "已完成"],
    ["failed", "失败"],
    ["cancelled", "已取消"],
    ["superseded", "结果已过期"],
  ] as const)("renders %s as a visible Chinese state", (status, label) => {
    render(<StatusPill status={status} />);
    expect(screen.getByText(label)).toBeVisible();
  });
});

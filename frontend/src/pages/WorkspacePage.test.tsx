import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { WorkspacePage } from "./WorkspacePage";

function renderWorkspace(path = "/inspections/i-1/regulations") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/inspections/:inspectionId/:section/*" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkspacePage", () => {
  beforeEach(() => {
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/inspections/i-1/") {
        return {
          id: "i-1",
          unit_name: "桐乡市示例商场",
          unit_address: "振兴路 1 号",
          inspection_at: "2026-08-24T09:00:00+08:00",
          inspection_type: "complaint_verification",
          owner: { id: "u-1", username: "检查员甲", role: "inspector" },
          revision: 3,
          processing_summary: { running: 1, failed: 0, succeeded: 4, queued: 0 },
        } as never;
      }
      return [] as never;
    });
  });

  it("supports a direct regulations route and exposes every required workbench tab", async () => {
    renderWorkspace();
    expect(await screen.findByRole("heading", { name: "桐乡市示例商场" })).toBeVisible();
    for (const label of [
      "材料",
      "转写",
      "事实与证据",
      "法规",
      "监督检查记录",
      "照片文书",
      "询问笔录",
      "历史",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeVisible();
    }
    expect(screen.getByRole("heading", { name: "法规检索" })).toBeVisible();
  });

  it("shows long-running work without blocking access to successful data", async () => {
    renderWorkspace("/inspections/i-1/materials");
    expect(await screen.findByText("1 项任务处理中")).toBeVisible();
    expect(await screen.findByRole("heading", { name: "检查材料" })).toBeVisible();
  });

  it("warns before a shared edit and submits the current shared revision", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue({} as never);
    renderWorkspace("/inspections/i-1/record");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "编辑共享信息" }));
    expect(screen.getByText(/会使本检查三类已确认文书同步退回初稿/)).toBeVisible();
    const unitName = screen.getByLabelText("单位名称");
    await user.clear(unitName);
    await user.type(unitName, "桐乡市复核商场");
    await user.click(screen.getByRole("button", { name: "保存共享信息" }));
    expect(screen.getByRole("heading", { name: "确认修改共享字段？" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认修改并使文书退回初稿" }));

    expect(patch).toHaveBeenCalledWith(
      "/inspections/i-1/",
      expect.objectContaining({
        unit_name: "桐乡市复核商场",
        inspection_at: "2026-08-24T01:00:00.000Z",
        shared_revision: 3,
      }),
      { revision: 3 },
    );
  });
});

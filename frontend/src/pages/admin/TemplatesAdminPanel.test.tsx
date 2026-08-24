import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { TemplatesAdminPanel } from "./TemplatesAdminPanel";

const confirmedTemplate = {
  id: "t-1",
  name: "消防监督检查记录模板",
  document_type: "record",
  active: true,
  recognition_status: "succeeded",
  created_at: "2026-08-24T00:00:00Z",
  mapping: {
    id: "map-1",
    revision_no: 1,
    state: "confirmed",
    regions: [{
      id: "r-1",
      display_name: "检查内容和情况",
      repeat_scope: "single",
      fill_strategy: "table_cell",
      confidence: 0.92,
      enabled: true,
      required: true,
      location_preview: "表格 / 第 3 行",
    }],
  },
} as const;

describe("TemplatesAdminPanel", () => {
  it("keeps a confirmed mapping read-only and creates a new draft for further changes", async () => {
    vi.spyOn(api, "get").mockImplementation(async (path) => (
      path === "/templates/" ? [confirmedTemplate] : confirmedTemplate
    ) as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({
      ...confirmedTemplate,
      mapping: { ...confirmedTemplate.mapping, id: "map-2", revision_no: 2, state: "draft" },
    } as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <TemplatesAdminPanel />
      </QueryClientProvider>,
    );

    expect(await screen.findByLabelText("业务名称")).toBeDisabled();
    expect(screen.getByText("只读")).toBeVisible();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "创建新映射草稿" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/templates/t-1/mapping/revise/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    ));
  });

  it("shows recognition job failure and queues a retry for only that job", async () => {
    const failedTemplate = {
      ...confirmedTemplate,
      id: "t-failed",
      name: "待识别模板",
      recognition_status: "failed",
      mapping: null,
      recognition_job: {
        id: "job-template-1",
        task_type: "template_recognition",
        status: "failed",
        safe_error_message: "上传模板内容与所选文书类型不一致。",
      },
    } as const;
    vi.spyOn(api, "get").mockImplementation(async (path) => (
      path === "/templates/" ? [failedTemplate] : failedTemplate
    ) as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({
      ...failedTemplate,
      recognition_status: "queued",
      recognition_job: { ...failedTemplate.recognition_job, status: "queued" },
    } as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <TemplatesAdminPanel />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("上传模板内容与所选文书类型不一致。")).toBeVisible();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "仅重试模板识别" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/templates/t-failed/recognize/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    ));
  });
});

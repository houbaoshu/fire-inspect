import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../api/client";
import type { DocumentType, Fact, InspectionDocument } from "../../api/types";
import {
  DOCUMENT_GENERATION_POLL_INTERVAL_MS,
  DocumentPanel,
  documentGenerationPollInterval,
} from "./DocumentPanel";

const inspection = {
  id: "i-1",
  unit_name: "示例商场",
  unit_address: "振兴路 1 号",
  inspection_at: "2026-08-24T09:00:00+08:00",
  inspection_type: "other",
  owner: { id: "u-1", username: "检查员甲", role: "inspector", status: "active" },
};

function documentFixture(overrides: Partial<InspectionDocument> = {}): InspectionDocument {
  return {
    id: "d-1",
    inspection_id: "i-1",
    document_type: "record",
    name: "示例商场监督检查记录",
    state: "draft",
    revision: 4,
    generator_status: "succeeded",
    generation_valid: true,
    fields: [{
      key: "record_number",
      display_name: "编号",
      value: "2026-001",
      origin: "manual",
      manual_locked: true,
    }],
    ...overrides,
  };
}

function renderDocument(
  current: InspectionDocument,
  type: DocumentType = current.document_type,
  loadDocument: () => InspectionDocument | Promise<InspectionDocument> = () => current,
  facts: Fact[] = [],
) {
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/inspections/i-1/") return inspection as never;
    if (path.startsWith("/inspections/i-1/documents/")) return [current] as never;
    if (path === `/documents/${current.id}/`) return loadDocument() as never;
    if (path === `/documents/${current.id}/photo-candidates/`) return [] as never;
    if (path === "/inspections/i-1/facts/") return facts as never;
    throw new Error(`unexpected GET ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DocumentPanel inspectionId="i-1" type={type} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DocumentPanel state protection", () => {
  it("polls queued and running generation at a bounded interval and stops at terminal states", () => {
    expect(documentGenerationPollInterval("queued")).toBe(DOCUMENT_GENERATION_POLL_INTERVAL_MS);
    expect(documentGenerationPollInterval("running")).toBe(DOCUMENT_GENERATION_POLL_INTERVAL_MS);
    expect(documentGenerationPollInterval("succeeded")).toBe(false);
    expect(documentGenerationPollInterval("failed")).toBe(false);
    expect(documentGenerationPollInterval("superseded")).toBe(false);
    expect(documentGenerationPollInterval("idle")).toBe(false);
    expect(documentGenerationPollInterval(undefined)).toBe(false);
  });

  it("automatically refreshes a queued document until the worker reports success", async () => {
    vi.useFakeTimers();
    try {
      const queued = documentFixture({ generator_status: "queued", generation_valid: false });
      const states = [
        queued,
        documentFixture({ generator_status: "running", generation_valid: false }),
        documentFixture({ generator_status: "succeeded", generation_valid: true, revision: 5 }),
      ];
      let detailReads = 0;
      renderDocument(queued, "record", async () => states[Math.min(detailReads++, 2)]);

      await vi.waitFor(() => expect(detailReads).toBe(1));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DOCUMENT_GENERATION_POLL_INTERVAL_MS);
      });
      await vi.waitFor(() => expect(detailReads).toBe(2));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DOCUMENT_GENERATION_POLL_INTERVAL_MS);
      });
      await vi.waitFor(() => expect(detailReads).toBe(3));
      expect(screen.getByRole("button", { name: "确认文书" })).toBeEnabled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(DOCUMENT_GENERATION_POLL_INTERVAL_MS * 3);
      });
      expect(detailReads).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("clears queued-document polling when the panel unmounts", async () => {
    vi.useFakeTimers();
    try {
      const queued = documentFixture({ generator_status: "queued", generation_valid: false });
      let detailReads = 0;
      const view = renderDocument(queued, "record", async () => {
        detailReads += 1;
        return queued;
      });
      await vi.waitFor(() => expect(detailReads).toBe(1));

      view.unmount();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DOCUMENT_GENERATION_POLL_INTERVAL_MS * 3);
      });
      expect(detailReads).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("protects manual content when explicitly regenerating a valid draft", async () => {
    const current = documentFixture();
    const post = vi.spyOn(api, "post").mockResolvedValue(current as never);
    renderDocument(current);

    expect(await screen.findByText("人工内容 · 已保护")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认文书" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "导出 DOCX" })).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "生成 / 重新生成初稿" }));
    expect(screen.getByText(/人工编辑并锁定的字段会默认保留/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保留人工内容并生成" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/documents/d-1/generate/",
      { expected_revision: 4, preserve_manual: true },
      expect.objectContaining({ revision: 4, idempotencyKey: expect.any(String) }),
    ));
  });

  it("keeps fields editable and offers an isolated retry after document generation fails", async () => {
    const current = documentFixture({
      generator_status: "failed",
      generator_job_id: "j-doc",
      generator_safe_error: "文书渲染服务暂时不可用。",
      generation_valid: false,
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    renderDocument(current);

    expect(await screen.findByText("文书渲染服务暂时不可用。")).toBeVisible();
    expect(screen.getByDisplayValue("2026-001")).toBeEnabled();
    expect(screen.queryByRole("button", { name: "生成 / 重新生成初稿" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认文书" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "仅重试生成" })).toBeEnabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "仅重试生成" }));
    expect(post).toHaveBeenCalledWith(
      "/jobs/j-doc/retry/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
  });

  it("preserves the last successful draft and manual editing path when regeneration request fails", async () => {
    const current = documentFixture();
    const post = vi.spyOn(api, "post").mockRejectedValue(
      new ApiError(503, "GENERATOR_UNAVAILABLE", "文书生成服务暂时不可用，请稍后重试。"),
    );
    renderDocument(current);

    const manualField = await screen.findByDisplayValue("2026-001");
    expect(manualField).toBeEnabled();
    expect(screen.getByText("人工内容 · 已保护")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认文书" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "导出 DOCX" })).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "生成 / 重新生成初稿" }));
    await user.click(screen.getByRole("button", { name: "保留人工内容并生成" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("文书生成服务暂时不可用，请稍后重试。");
    expect(post).toHaveBeenCalledWith(
      "/documents/d-1/generate/",
      { expected_revision: 4, preserve_manual: true },
      expect.objectContaining({ revision: 4, idempotencyKey: expect.any(String) }),
    );
    expect(screen.getByDisplayValue("2026-001")).toBeEnabled();
    expect(screen.getByText("人工内容 · 已保护")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认文书" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "导出 DOCX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成 / 重新生成初稿" })).toBeEnabled();
  });

  it("does not allow background regeneration or draft export after human confirmation", async () => {
    renderDocument(documentFixture({ state: "confirmed" }));
    expect(await screen.findByText(/此文书已由用户确认/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成 / 重新生成初稿" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认文书" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出 DOCX" })).toBeEnabled();
  });

  it("renders only same-origin protected photo preview URLs", async () => {
    const current = documentFixture({
      document_type: "photo",
      fields: [],
      photo_items: [{
        id: "p-1",
        sequence: 0,
        thumbnail_url: "/api/v1/materials/m-1/preview/",
        reflected_content: "现场照片",
        description: "疏散通道",
      }],
    });
    const view = renderDocument(current, "photo");
    const preview = await screen.findByAltText("照片 1");
    expect(preview).toHaveAttribute("src", "/api/v1/materials/m-1/preview/");
    expect(view.container.querySelector('img[src^="http://"], img[src^="https://"]')).not.toBeInTheDocument();
  });

  it("shows confirmed fact dependencies and saves an explicit user selection", async () => {
    const facts: Fact[] = [
      {
        id: "f-1",
        inspection_id: "i-1",
        content: "消防控制室值班情况需要核实。",
        review_state: "confirmed",
        needs_verification: true,
        origin: "manual",
      },
      {
        id: "f-2",
        inspection_id: "i-1",
        content: "大厅存在堆放物品。",
        review_state: "confirmed",
        needs_verification: false,
        origin: "manual",
      },
    ];
    const current = documentFixture({ fact_dependency_ids: ["f-1"] });
    const put = vi.spyOn(api, "put").mockResolvedValue(current as never);
    renderDocument(current, "record", () => current, facts);

    expect(await screen.findByRole("heading", { name: "引用的已确认事实" })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /消防控制室值班情况/ })).toBeChecked();
    const second = screen.getByRole("checkbox", { name: /大厅存在堆放物品/ });
    expect(second).not.toBeChecked();
    await userEvent.setup().click(second);
    await userEvent.setup().click(screen.getByRole("button", { name: "保存事实依赖" }));

    await waitFor(() => expect(put).toHaveBeenCalledWith(
      "/documents/d-1/fact-dependencies/",
      { fact_ids: ["f-1", "f-2"] },
      expect.objectContaining({ revision: 4 }),
    ));
  });

  it("exposes interview QA sync, add, reorder, and delete controls", async () => {
    const current = documentFixture({
      document_type: "interview",
      fields: [],
      qa_items: [
        {
          id: "qa-1",
          sequence: 1,
          text: "现场情况如何？",
          suggested_role: "question",
          confirmed_role: "question",
          content_origin: "manual",
        },
        {
          id: "qa-2",
          sequence: 2,
          text: "大厅存在堆放物品。",
          suggested_role: "answer",
          confirmed_role: "answer",
          content_origin: "ai",
        },
      ],
    });
    const post = vi.spyOn(api, "post").mockResolvedValue(current as never);
    const put = vi.spyOn(api, "put").mockResolvedValue(current as never);
    const remove = vi.spyOn(api, "delete").mockResolvedValue(current as never);
    renderDocument(current, "interview");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "同步转写候选" }));
    expect(post).toHaveBeenCalledWith(
      "/documents/d-1/qa-items/sync/",
      {},
      expect.objectContaining({ revision: 4 }),
    );

    await user.click(screen.getAllByRole("button", { name: "下移" })[0]);
    expect(put).toHaveBeenCalledWith(
      "/documents/d-1/qa-items/",
      { item_ids: ["qa-2", "qa-1"] },
      expect.objectContaining({ revision: 4 }),
    );

    await user.click(screen.getAllByRole("button", { name: "删除问答" })[0]);
    expect(remove).toHaveBeenCalledWith(
      "/documents/d-1/qa-items/qa-1/",
      {},
      expect.objectContaining({ revision: 4 }),
    );

    await user.click(screen.getByRole("button", { name: "新增问答" }));
    await user.selectOptions(screen.getByLabelText("新增角色"), "answer");
    await user.type(screen.getByLabelText("新增正文"), "人工补充回答。");
    await user.click(screen.getByRole("button", { name: "添加问答" }));
    expect(post).toHaveBeenCalledWith(
      "/documents/d-1/qa-items/",
      { text: "人工补充回答。", confirmed_role: "answer" },
      expect.objectContaining({ revision: 4 }),
    );
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { KnowledgeAdminPanel } from "./KnowledgeAdminPanel";

describe("KnowledgeAdminPanel", () => {
  it("locks a declared internal policy to the internal category before upload", async () => {
    vi.spyOn(api, "get").mockResolvedValue([] as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <KnowledgeAdminPanel />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "＋ 上传知识文档" }));
    const file = new File(["PK test"], "内部值班制度.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    await user.upload(screen.getByLabelText("PDF / DOCX 文件"), file);
    await user.click(screen.getByLabelText("我确认这份文件属于机构内部制度"));
    const category = screen.getByRole("combobox", { name: /文档类别/ });
    expect(category).toBeDisabled();
    expect(category).toHaveValue("internal");
    const submit = screen.getByRole("button", { name: "上传并排队解析" });
    fireEvent.submit(submit.closest("form")!);

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [path, body] = post.mock.calls[0];
    expect(path).toBe("/knowledge/documents/");
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get("category")).toBe("internal");
    expect(form.get("declared_internal")).toBe("true");
    expect(form.get("source_verification")).toBe("pending");
    expect(form.has("publication_date")).toBe(false);
  });

  it("shows the persistent parse failure and retries only that failed job", async () => {
    const failed = {
      id: "knowledge-1",
      title: "待分段制度",
      category: "other",
      source_verification: "pending",
      validity: "unknown",
      processing_status: "failed",
      clause_count: 0,
      processing_job: {
        id: "job-knowledge-1",
        task_type: "knowledge_parse",
        status: "failed",
        safe_error_message: "未识别到可用条款，请人工检查原文后重试。",
      },
    } as const;
    vi.spyOn(api, "get").mockResolvedValue([failed] as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({
      ...failed,
      processing_status: "pending",
      processing_job: { ...failed.processing_job, status: "queued", safe_error_message: "" },
    } as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <KnowledgeAdminPanel />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("未识别到可用条款，请人工检查原文后重试。")).toBeVisible();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "仅重试解析" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/knowledge/documents/knowledge-1/retry/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    ));
  });
});

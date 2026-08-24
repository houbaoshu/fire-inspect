import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { JobsAdminPanel } from "./JobsAdminPanel";

describe("JobsAdminPanel", () => {
  it("shows a safe embedding failure and retries only that failed job", async () => {
    vi.spyOn(api, "get").mockResolvedValue([{
      id: "j-embedding",
      task_type: "embedding",
      status: "failed",
      safe_error_message: "向量服务暂时不可用，原始知识文档已保留。",
      attempt_count: 1,
      max_attempts: 3,
      created_at: "2026-08-24T00:00:00Z",
    }] as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(<QueryClientProvider client={queryClient}><JobsAdminPanel /></QueryClientProvider>);

    expect(await screen.findByText("向量服务暂时不可用，原始知识文档已保留。")).toBeVisible();
    expect(screen.getByText("向量索引", { selector: "strong" })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "仅重试失败步骤" }));
    expect(post).toHaveBeenCalledWith(
      "/jobs/j-embedding/retry/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
  });
});

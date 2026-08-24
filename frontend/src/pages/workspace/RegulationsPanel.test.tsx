import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { RegulationsPanel } from "./RegulationsPanel";

function renderPanel(path = "/inspections/i-1/regulations") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RegulationsPanel inspectionId="i-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RegulationsPanel", () => {
  it("uses GET q search and renders original text with its source state", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      found: true,
      message: "已找到可能相关的原文，适用性需人工确认。",
      results: [{
        clause_id: "c-1",
        clause_identifier: "第十八条",
        original_text: "消防控制室实行每日二十四小时值班制度。",
        applicability_reason: "与消防控制室值班问题相关。",
        document_id: "k-1",
        document_title: "测试消防法规",
        source_status: "pending",
        page_start: 3,
      }],
    } as never);
    renderPanel();
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText(/输入问题、条款号或关键词/), "消防控制室 值班");
    await user.click(screen.getByRole("button", { name: "检索法规" }));

    expect(api.get).toHaveBeenCalledWith("/knowledge/search/?q=%E6%B6%88%E9%98%B2%E6%8E%A7%E5%88%B6%E5%AE%A4%20%E5%80%BC%E7%8F%AD");
    expect(await screen.findByText("消防控制室实行每日二十四小时值班制度。")).toBeVisible();
    expect(screen.getByRole("heading", { name: "测试消防法规", level: 3 })).toBeVisible();
    expect(screen.getByText("与消防控制室值班问题相关。")).toBeVisible();
    expect(screen.getByText(/第 3 页/)).toBeVisible();
    expect(screen.getByText("待核验")).toBeVisible();
    expect(screen.getByRole("link", { name: "打开原始文档" })).toHaveAttribute(
      "href",
      "/api/v1/knowledge/documents/k-1/download/",
    );
  });

  it("shows an explicit not-found response instead of fabricated clauses", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      found: false,
      message: "未找到可靠的相关条款，请调整查询或人工核验。",
      results: [],
    } as never);
    renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/输入问题、条款号或关键词/), "不存在的测试条款");
    await user.click(screen.getByRole("button", { name: "检索法规" }));

    expect(await screen.findByRole("heading", { name: "未找到可靠条款" })).toBeVisible();
    expect(screen.getByText("未找到可靠的相关条款，请调整查询或人工核验。")).toBeVisible();
  });

  it("opens a saved clause directly and preserves verified source metadata", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      clause_id: "c-1",
      clause_identifier: "第十八条",
      original_text: "保存的条款原文。",
      document_id: "k-1",
      document_title: "已核验法规",
      source_verification: "verified",
    } as never);
    renderPanel("/inspections/i-1/regulations?document=k-1&clause=c-1");

    expect(await screen.findByText("保存的条款原文。")).toBeVisible();
    expect(screen.getByText("来源已核验")).toBeVisible();
    expect(api.get).toHaveBeenCalledWith("/knowledge/documents/k-1/clauses/c-1/");
  });
});

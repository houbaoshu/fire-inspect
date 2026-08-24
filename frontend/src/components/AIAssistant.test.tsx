import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { AssistantResponse } from "../api/types";
import { ApiError } from "../api/client";
import { AIAssistant, type AssistantSelection } from "./AIAssistant";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderAssistant(response: AssistantResponse, selection?: AssistantSelection) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const query = vi.fn().mockResolvedValue(response);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/inspections/i-1/facts"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <AIAssistant
                  inspectionId="i-1"
                  route="/inspections/i-1/facts"
                  selection={selection}
                  query={query}
                />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return query;
}

describe("AIAssistant", () => {
  it("submits the server contract and performs a typed same-inspection navigation", async () => {
    const query = renderAssistant({
      text: "候选事实在视频 1 的 12 秒附近。",
      citations: [{ source_type: "fact", source_id: "f-1", excerpt: "疏散通道需要核实" }],
      navigation: [
        {
          kind: "material_time",
          target_id: "e-1",
          inspection_id: "i-1",
          material_id: "m-1",
          start_ms: 12_000,
        },
      ],
      read_only: true,
    }, { sourceId: "f-1", revision: 4, text: "疏散通道需要核实" });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("向 AI 助手提问"), "值班情况的证据在哪里？");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("候选事实在视频 1 的 12 秒附近。")).toBeVisible();
    expect(query).toHaveBeenCalledWith(
      {
        inspection_id: "i-1",
        question: "值班情况的证据在哪里？",
        route: "/inspections/i-1/facts",
        selected_source_id: "f-1",
        selected_revision: 4,
        selected_text: "疏散通道需要核实",
      },
    );
    expect(screen.queryByText("材料转写")).not.toBeInTheDocument();
    expect(screen.getByText("检查事实")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "打开材料时间" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/inspections/i-1/materials?material=m-1&t=12000",
    );
  });

  it("refuses cross-inspection and untyped navigation returned by AI", async () => {
    renderAssistant({
      text: "我不能打开无权访问的目标。",
      citations: [],
      navigation: [
        {
          kind: "fact",
          target_id: "f-2",
          inspection_id: "i-2",
          fact_id: "f-2",
        },
        {
          kind: "material_time",
          target_id: "e-2",
          inspection_id: "i-1",
          material_id: "m-2",
          start_ms: Number.NaN,
        },
      ],
      read_only: true,
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("向 AI 助手提问"), "打开目标");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("我不能打开无权访问的目标。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开事实" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开材料时间" })).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/inspections/i-1/facts");
  });

  it("routes a fully identified clause without accepting a guessed parent document", async () => {
    renderAssistant({
      text: "相关条款如下。",
      citations: [{ source_type: "clause", source_id: "c-1", excerpt: "消防控制室实行每日二十四小时值班制度" }],
      navigation: [{
        kind: "clause",
        target_id: "c-1",
        inspection_id: "i-1",
        clause_id: "c-1",
        document_id: "k-1",
      }],
      read_only: true,
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("向 AI 助手提问"), "打开条款");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.click(await screen.findByRole("button", { name: "打开法规条款" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/inspections/i-1/regulations?document=k-1&clause=c-1",
    );
  });

  it("shows a safe failure without inventing an answer or navigation", async () => {
    const query = renderAssistant({ text: "", citations: [], navigation: [], read_only: true });
    query.mockRejectedValueOnce(new ApiError(503, "assistant_unavailable", "只读助手尚未配置，请联系管理员。"));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("向 AI 助手提问"), "总结当前检查");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("只读助手尚未配置，请联系管理员。");
    expect(screen.queryByText("助手回答")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^打开/ })).not.toBeInTheDocument();
  });

  it("does not execute navigation unless the server marks the response read-only", async () => {
    renderAssistant({
      text: "响应模式异常。",
      citations: [],
      navigation: [{
        kind: "fact",
        target_id: "f-1",
        inspection_id: "i-1",
        fact_id: "f-1",
      }],
      read_only: false,
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("向 AI 助手提问"), "打开事实");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("响应模式异常。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开事实" })).not.toBeInTheDocument();
  });
});

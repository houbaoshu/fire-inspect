import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { Fact } from "../../api/types";
import { FactsPanel } from "./FactsPanel";

function renderPanel(fact: Fact) {
  vi.spyOn(api, "get").mockResolvedValue([fact] as never);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/inspections/i-1/facts"]}>
        <FactsPanel inspectionId="i-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const fact: Fact = {
  id: "f-1",
  inspection_id: "i-1",
  content: "疏散通道堆放杂物。",
  review_state: "candidate",
  needs_verification: false,
  origin: "manual",
  revision: 3,
  evidence: [],
};

describe("FactsPanel", () => {
  it("requires a reason and sends explicit confirmation when deleting a fact", async () => {
    const deleteCall = vi.spyOn(api, "delete").mockResolvedValue(undefined as never);
    renderPanel(fact);
    const user = userEvent.setup();
    expect(await screen.findByText(fact.content)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "删除" }));
    const confirm = screen.getByRole("button", { name: "确认删除事实" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/原因/), "现场复核后确认录入有误");
    await user.click(confirm);

    await waitFor(() => expect(deleteCall).toHaveBeenCalledWith(
      "/inspections/i-1/facts/f-1/",
      { confirm: true, reason: "现场复核后确认录入有误", revision: 3 },
      expect.objectContaining({ revision: 3, idempotencyKey: expect.any(String) }),
    ));
  });

  it("offers a withdrawn fact only as a candidate restoration", async () => {
    renderPanel({ ...fact, review_state: "withdrawn" });
    expect(await screen.findByRole("button", { name: "恢复为候选" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "人工确认事实" })).not.toBeInTheDocument();
  });

  it("routes media evidence with its own material ID and original timeline", async () => {
    renderPanel({
      ...fact,
      evidence: [{
        id: "e-1",
        type: "media_range",
        label: "视频 4 · 大厅堆放物品",
        material_id: "m-video-4",
        start_ms: 12_345,
        end_ms: 15_000,
      }],
    });
    const link = await screen.findByRole("link", { name: /视频 4 · 大厅堆放物品/ });
    expect(link).toHaveAttribute(
      "href",
      "/inspections/i-1/materials?material=m-video-4&t=12345",
    );
  });
});

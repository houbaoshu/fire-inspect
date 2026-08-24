import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { HistoryPanel } from "./HistoryPanel";

describe("HistoryPanel", () => {
  it("shows actor, time, reason, old values and new values for an inspection-scoped change", async () => {
    vi.spyOn(api, "get").mockResolvedValue([{
      id: "h-1",
      object_type: "fact",
      object_label: "现场事实",
      action: "withdrawn",
      actor: "检查员甲",
      occurred_at: "2026-08-24T01:00:00Z",
      reason: "现场复核后需要补充证据",
      old_values: { review_state: "confirmed" },
      new_values: { review_state: "withdrawn" },
    }] as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={queryClient}><HistoryPanel inspectionId="i-1" /></QueryClientProvider>);

    expect(await screen.findByText("检查员甲")).toBeVisible();
    expect(screen.getByText("原因：现场复核后需要补充证据")).toBeVisible();
    await userEvent.setup().click(screen.getByText("查看旧值与新值"));
    expect(screen.getByText(/confirmed/)).toBeVisible();
    expect(screen.getByText("withdrawn", { selector: ".history-actor span" })).toBeVisible();
    expect(api.get).toHaveBeenCalledWith("/inspections/i-1/history/");
  });
});

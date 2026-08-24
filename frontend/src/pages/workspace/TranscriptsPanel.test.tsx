import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { TranscriptsPanel } from "./TranscriptsPanel";

describe("TranscriptsPanel", () => {
  it("saves edited text before confirming a role with the incremented transcript revision", async () => {
    vi.spyOn(api, "get").mockResolvedValue([{
      id: "tr-1",
      material_id: "m-1",
      material_name: "询问录音.wav",
      full_text: "问：现场情况？",
      full_text_origin: "manual",
      revision: 5,
      segments: [{
        id: "s-1",
        sequence: 0,
        start_ms: 0,
        end_ms: 2_000,
        text: "现场情况",
        suggested_role: "question",
        confirmed_role: null,
      }],
    }] as never);
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ transcript_revision: 6 } as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({ transcript_revision: 7 } as never);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <TranscriptsPanel inspectionId="i-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    expect(await screen.findByText("人工来源 / 人工可改")).toBeVisible();
    const segmentText = await screen.findByLabelText("片段 1 文字");
    await user.clear(segmentText);
    await user.type(segmentText, "现场存在堆放物品");
    await user.selectOptions(screen.getByLabelText("片段 1 角色"), "answer");
    await user.click(screen.getByRole("button", { name: "保存片段" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      "/transcripts/tr-1/segments/s-1/",
      { text: "现场存在堆放物品", transcript_revision: 5 },
      { revision: 5 },
    ));
    expect(post).toHaveBeenCalledWith(
      "/transcripts/tr-1/segments/s-1/confirm-role/",
      { role: "answer", transcript_revision: 6 },
      { revision: 6 },
    );
  });
});

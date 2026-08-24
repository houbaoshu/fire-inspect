import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../api/client";
import { MaterialsPanel } from "./MaterialsPanel";

function renderPanel(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <MaterialsPanel inspectionId="i-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MaterialsPanel evidence navigation", () => {
  it("selects the linked material and seeks to the original evidence time", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-video-1",
        inspection_id: "i-1",
        kind: "video",
        display_name: "视频 1.mp4",
        duration_ms: 30_000,
        revision: 1,
      },
      {
        id: "m-video-4",
        inspection_id: "i-1",
        kind: "video",
        display_name: "视频 4.mp4",
        duration_ms: 30_000,
        revision: 2,
      },
    ] as never);

    const view = renderPanel("/inspections/i-1/materials?material=m-video-4&t=12345");
    const video = await waitFor(() => {
      const element = view.container.querySelector("video");
      expect(element).toBeInTheDocument();
      return element as HTMLVideoElement;
    });

    expect(video).toHaveAttribute("src", "/api/v1/materials/m-video-4/play/");
    fireEvent.loadedMetadata(video);
    expect(Math.abs(video.currentTime * 1000 - 12_345)).toBeLessThanOrEqual(3_000);
  });

  it("does not silently play the first material when the requested target is missing", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-first",
        inspection_id: "i-1",
        kind: "video",
        display_name: "不应回退播放的视频.mp4",
        duration_ms: 30_000,
        revision: 1,
      },
    ] as never);

    const view = renderPanel("/inspections/i-1/materials?material=m-missing&t=12000");

    expect(await screen.findByText(/指定材料不存在或你无权访问/)).toBeVisible();
    expect(view.container.querySelector("video")).not.toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/inspections/i-1/materials/");
  });

  it("keeps successful steps and the original player while retrying only the failed step", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-1",
        inspection_id: "i-1",
        kind: "video",
        display_name: "现场视频.mp4",
        duration_ms: 30_000,
        revision: 2,
        steps: [
          { name: "asr", status: "failed", job_id: "j-asr", safe_error_message: "中文转写服务暂时不可用。" },
          { name: "vision", status: "succeeded", job_id: "j-vision" },
        ],
      },
    ] as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    const view = renderPanel("/inspections/i-1/materials?material=m-1");

    expect(await screen.findByText("中文转写服务暂时不可用。")).toBeVisible();
    expect(view.container.querySelector("video")).toBeInTheDocument();
    expect(screen.getAllByText("已完成")).toHaveLength(1);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "仅重试此步骤" }));
    expect(post).toHaveBeenCalledWith(
      "/jobs/j-asr/retry/",
      {},
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
  });

  it("offers video transcoding and audio extraction as independent jobs", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-1",
        inspection_id: "i-1",
        kind: "video",
        display_name: "待处理视频.mp4",
        duration_ms: 30_000,
        revision: 7,
        steps: [],
      },
    ] as never);
    const post = vi.spyOn(api, "post").mockResolvedValue({} as never);
    renderPanel("/inspections/i-1/materials?material=m-1");

    const transcodeRow = (await screen.findByText("兼容播放")).closest(".step-row");
    const audioExtractRow = screen.getByText("音频提取").closest(".step-row");
    expect(transcodeRow).not.toBeNull();
    expect(audioExtractRow).not.toBeNull();
    const user = userEvent.setup();
    await user.click(within(transcodeRow as HTMLElement).getByRole("button", { name: "开始处理" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/materials/m-1/jobs/transcode/",
      {},
      expect.objectContaining({ revision: 7, idempotencyKey: expect.any(String) }),
    ));
    await waitFor(() => expect(within(audioExtractRow as HTMLElement).getByRole("button", { name: "开始处理" })).toBeEnabled());
    await user.click(within(audioExtractRow as HTMLElement).getByRole("button", { name: "开始处理" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      "/materials/m-1/jobs/audio_extract/",
      {},
      expect.objectContaining({ revision: 7, idempotencyKey: expect.any(String) }),
    ));
  });

  it("shows only processing steps that can run for an image material", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-image",
        inspection_id: "i-1",
        kind: "image",
        display_name: "现场照片.jpg",
        revision: 1,
        steps: [],
      },
    ] as never);
    renderPanel("/inspections/i-1/materials?material=m-image");

    expect(await screen.findByText("画面描述")).toBeVisible();
    expect(screen.queryByText("候选事实")).not.toBeInTheDocument();
    expect(screen.queryByText("关键画面")).not.toBeInTheDocument();
    expect(screen.queryByText("语音转写")).not.toBeInTheDocument();
  });

  it("shows direct fact extraction as the only processing step for text material", async () => {
    vi.spyOn(api, "get").mockResolvedValue([
      {
        id: "m-text",
        inspection_id: "i-1",
        kind: "text",
        display_name: "现场说明.txt",
        revision: 1,
        steps: [],
      },
    ] as never);
    renderPanel("/inspections/i-1/materials?material=m-text");

    expect(await screen.findByText("候选事实")).toBeVisible();
    expect(screen.queryByText("画面描述")).not.toBeInTheDocument();
    expect(screen.queryByText("语音转写")).not.toBeInTheDocument();
  });

  it("creates a clearly marked manual transcript and refreshes both material states", async () => {
    const material = {
      id: "m-1",
      inspection_id: "i-1",
      kind: "audio",
      display_name: "现场录音.wav",
      duration_ms: 30_000,
      revision: 3,
      steps: [{ name: "asr", status: "failed", job_id: "j-asr" }],
    };
    const transcript = {
      id: "tr-manual",
      material_id: "m-1",
      material_name: "现场录音.wav",
      full_text: "人工补充：现场情况需要继续核实。",
      full_text_origin: "manual",
      revision: 1,
      segments: [],
    };
    let created = false;
    const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/inspections/i-1/materials/") return [material] as never;
      if (path === "/inspections/i-1/transcripts/") return (created ? [transcript] : []) as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const put = vi.spyOn(api, "put").mockImplementation(async () => {
      created = true;
      return transcript as never;
    });
    renderPanel("/inspections/i-1/materials?material=m-1");

    expect(await screen.findByText("自动转写失败时，原始材料仍会保留；你可以在这里录入连续全文。")).toBeVisible();
    expect(screen.getByText("将保存为人工来源")).toBeVisible();
    const input = screen.getByLabelText("现场录音.wav 人工连续转写");
    const user = userEvent.setup();
    await user.type(input, "人工补充：现场情况需要继续核实。");
    await user.click(screen.getByRole("button", { name: "保存人工转写" }));

    await waitFor(() => expect(put).toHaveBeenCalledWith(
      "/materials/m-1/transcript",
      { full_text: "人工补充：现场情况需要继续核实。" },
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    ));
    expect(await screen.findByText(/该材料已有人工转写/)).toBeVisible();
    expect(screen.getByText("人工来源")).toBeVisible();
    expect(screen.queryByLabelText("现场录音.wav 人工连续转写")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(get.mock.calls.filter(([path]) => path === "/inspections/i-1/materials/")).toHaveLength(2);
      expect(get.mock.calls.filter(([path]) => path === "/inspections/i-1/transcripts/")).toHaveLength(2);
    });
  });

  it("keeps the manual transcript draft when a 409 race is returned", async () => {
    const material = {
      id: "m-1",
      inspection_id: "i-1",
      kind: "video",
      display_name: "现场视频.mp4",
      duration_ms: 30_000,
      revision: 3,
      steps: [{ name: "asr", status: "failed", job_id: "j-asr" }],
    };
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/inspections/i-1/materials/") return [material] as never;
      if (path === "/inspections/i-1/transcripts/") return [] as never;
      throw new Error(`unexpected GET ${path}`);
    });
    vi.spyOn(api, "put").mockRejectedValue(
      new ApiError(409, "transcript_already_exists", "该材料已有转写，请在现有转写上编辑"),
    );
    renderPanel("/inspections/i-1/materials?material=m-1");

    const input = await screen.findByLabelText("现场视频.mp4 人工连续转写");
    const user = userEvent.setup();
    await user.type(input, "这段人工录入不能因请求失败而消失。");
    await user.click(screen.getByRole("button", { name: "保存人工转写" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该材料已有转写，请在现有转写上编辑");
    expect(input).toHaveValue("这段人工录入不能因请求失败而消失。");
    expect(screen.getByRole("button", { name: "保存人工转写" })).toBeEnabled();
  });
});

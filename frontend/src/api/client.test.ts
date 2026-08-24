import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./client";

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

describe("ApiClient", () => {
  afterEach(() => {
    document.cookie = "csrftoken=; Max-Age=0; path=/";
  });

  it("uses same-origin cookies without browser-stored bearer tokens", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "inspection-1" }));

    const client = new ApiClient("/api/v1");
    await client.get("/inspections/");

    const [, init] = fetchMock.mock.calls[0];
    expect(init).toMatchObject({ credentials: "same-origin" });
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  it("adds Django CSRF, revision and idempotency headers to writes", async () => {
    document.cookie = "csrftoken=csrf-value; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ ok: true }));

    const client = new ApiClient("/api/v1");
    await client.patch(
      "/inspections/inspection-1/",
      { unit_name: "新单位" },
      { revision: 7, idempotencyKey: "edit-1" },
    );

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("X-CSRFToken")).toBe("csrf-value");
    expect(headers.get("If-Match")).toBe('"7"');
    expect(headers.get("Idempotency-Key")).toBe("edit-1");
  });

  it("sends the create-only manual transcript PUT contract and accepts 201", async () => {
    document.cookie = "csrftoken=csrf-value; path=/";
    const created = {
      id: "transcript-1",
      material_id: "material-1",
      full_text: "人工补充：现场情况需要继续核实。",
      full_text_origin: "manual",
      segments: [],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(created, 201));
    const client = new ApiClient("/api/v1");

    await expect(client.put(
      "/materials/material-1/transcript",
      { full_text: "人工补充：现场情况需要继续核实。" },
      { idempotencyKey: "manual-transcript-1" },
    )).resolves.toEqual(created);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/materials/material-1/transcript");
    expect(init).toMatchObject({
      method: "PUT",
      credentials: "same-origin",
      body: JSON.stringify({ full_text: "人工补充：现场情况需要继续核实。" }),
    });
    const headers = new Headers(init?.headers);
    expect(headers.get("X-CSRFToken")).toBe("csrf-value");
    expect(headers.get("Idempotency-Key")).toBe("manual-transcript-1");
  });

  it("exposes only the server's safe public error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { code: "REVISION_CONFLICT", message: "数据已被其他用户修改，请刷新后重试" },
        409,
      ),
    );

    const client = new ApiClient("/api/v1");
    await expect(client.get("/inspections/inspection-1/")).rejects.toEqual(
      expect.objectContaining({
        status: 409,
        code: "REVISION_CONFLICT",
        message: "数据已被其他用户修改，请刷新后重试",
      }),
    );
  });

  it("parses the backend's nested stable error envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "assistant_unavailable",
            message: "只读助手尚未配置，请联系管理员。",
            details: { route: ["当前页面不受支持"] },
          },
        },
        503,
      ),
    );

    const client = new ApiClient("/api/v1");
    await expect(client.post("/assistant/query", {})).rejects.toEqual(
      expect.objectContaining({
        status: 503,
        code: "assistant_unavailable",
        message: "只读助手尚未配置，请联系管理员。",
        errors: { route: ["当前页面不受支持"] },
      }),
    );
  });

  it("notifies the application when a session becomes unauthorized", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "not_authenticated", message: "请先登录。" } }, 401),
    );
    const unauthorized = vi.fn();
    window.addEventListener("fire-inspect:unauthorized", unauthorized);
    const client = new ApiClient("/api/v1");

    await expect(client.get("/inspections/")).rejects.toMatchObject({ status: 401 });
    expect(unauthorized).toHaveBeenCalledOnce();
    window.removeEventListener("fire-inspect:unauthorized", unauthorized);
  });
});

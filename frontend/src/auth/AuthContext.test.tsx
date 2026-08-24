import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../api/client";
import { AuthProvider, useAuth } from "./AuthContext";

function AuthProbe() {
  const { status, user, login } = useAuth();
  return (
    <div>
      <output>{status}:{user?.username ?? "none"}</output>
      <button type="button" onClick={() => void login("inspector_a", "secure-password")}>测试登录</button>
    </div>
  );
}

describe("AuthProvider", () => {
  afterEach(() => {
    document.cookie = "csrftoken=; Max-Age=0; path=/";
  });

  it("obtains a CSRF cookie endpoint before the first session login", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path === "/auth/me/") throw new ApiError(401, "not_authenticated", "请先登录。");
      if (path === "/auth/csrf/") return { csrf_token: "token" } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({
      user: { id: "u-1", username: "inspector_a", role: "inspector", status: "active" },
    } as never);

    render(<AuthProvider><AuthProbe /></AuthProvider>);
    expect(await screen.findByText("anonymous:none")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "测试登录" }));

    expect(await screen.findByText("authenticated:inspector_a")).toBeVisible();
    expect(get).toHaveBeenCalledWith("/auth/csrf/");
    expect(post).toHaveBeenCalledWith("/auth/login/", {
      username: "inspector_a",
      password: "secure-password",
    });
    const csrfOrder = get.mock.invocationCallOrder.find((_, index) => get.mock.calls[index][0] === "/auth/csrf/");
    expect(csrfOrder).toBeLessThan(post.mock.invocationCallOrder[0]);
  });
});
